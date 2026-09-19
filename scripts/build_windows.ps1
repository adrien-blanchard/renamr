[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipDependencyInstall,
    [switch]$SkipInstaller,
    [string]$IsccPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$appName = 'Renamr'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$entryPoint = Join-Path $projectRoot 'batch_renamer.py'
$pyProjectPath = Join-Path $projectRoot 'pyproject.toml'
$versionModulePath = Join-Path $projectRoot 'sequence_renamer\__init__.py'
$versionInfoPath = Join-Path $projectRoot 'packaging\version_info.txt'
$innoScriptPath = Join-Path $projectRoot 'packaging\Renamr.iss'
$iconPath = Join-Path $projectRoot 'assets\Renamr.ico'
$iconGeneratorPath = Join-Path $projectRoot 'scripts\generate_icon.py'
$runtimeAssetsPath = Join-Path $projectRoot 'sequence_renamer\assets'
$runtimeSvgPath = Join-Path $runtimeAssetsPath 'renamr.svg'
$buildVenvPath = Join-Path $projectRoot '.venv-build313'
$buildPython = Join-Path $buildVenvPath 'Scripts\python.exe'
$artifactRoot = Join-Path $projectRoot 'artifacts'

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$CommandArguments,

        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    Write-Host "==> $Description"
    & $FilePath @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Resolve-InnoCompiler {
    param([string]$RequestedPath)

    if ($RequestedPath) {
        $resolvedRequestedPath = [System.IO.Path]::GetFullPath($RequestedPath)
        if (-not (Test-Path -LiteralPath $resolvedRequestedPath -PathType Leaf)) {
            throw "The requested Inno Setup compiler does not exist: $resolvedRequestedPath"
        }
        return $resolvedRequestedPath
    }

    $isccCommand = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
    if ($isccCommand) {
        return $isccCommand.Source
    }

    $candidatePaths = @()
    if (${env:ProgramFiles(x86)}) {
        $candidatePaths += Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
    }
    if ($env:ProgramFiles) {
        $candidatePaths += Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'
    }
    if ($env:LOCALAPPDATA) {
        $candidatePaths += Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
    }

    foreach ($candidatePath in $candidatePaths) {
        if (Test-Path -LiteralPath $candidatePath -PathType Leaf) {
            return [System.IO.Path]::GetFullPath($candidatePath)
        }
    }

    return $null
}

function Get-RegexCapture {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Pattern,

        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    $content = [System.IO.File]::ReadAllText($Path)
    $match = [System.Text.RegularExpressions.Regex]::Match(
        $content,
        $Pattern,
        [System.Text.RegularExpressions.RegexOptions]::Multiline
    )
    if (-not $match.Success -or $match.Groups.Count -lt 2) {
        throw "Could not read $Description from $Path."
    }
    return $match.Groups[1].Value
}

function ConvertTo-FourPartVersion {
    param(
        [Parameter(Mandatory = $true)]
        [string]$VersionText,

        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    try {
        $parsed = [System.Version]::Parse($VersionText)
    }
    catch {
        throw "$Description must be a numeric version with one to four components; got '$VersionText'."
    }
    $build = if ($parsed.Build -ge 0) { $parsed.Build } else { 0 }
    $revision = if ($parsed.Revision -ge 0) { $parsed.Revision } else { 0 }
    return "$($parsed.Major).$($parsed.Minor).$build.$revision"
}

function Get-VersionInfoTuple {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$FieldName
    )

    $content = [System.IO.File]::ReadAllText($Path)
    $escapedFieldName = [System.Text.RegularExpressions.Regex]::Escape($FieldName)
    $pattern = "^\s*$escapedFieldName=\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)"
    $match = [System.Text.RegularExpressions.Regex]::Match(
        $content,
        $pattern,
        [System.Text.RegularExpressions.RegexOptions]::Multiline
    )
    if (-not $match.Success) {
        throw "Could not read $FieldName from $Path."
    }
    $parts = 1..4 | ForEach-Object { $match.Groups[$_].Value }
    return $parts -join '.'
}

function Assert-VersionConsistency {
    param([Parameter(Mandatory = $true)][string]$ExpectedVersion)

    $expectedNormalized = ConvertTo-FourPartVersion -VersionText $ExpectedVersion -Description 'pyproject.toml project version'
    $declaredVersions = [ordered]@{
        'sequence_renamer.__version__' = Get-RegexCapture -Path $versionModulePath -Pattern '^__version__\s*=\s*"([^"]+)"\s*$' -Description 'sequence_renamer.__version__'
        'Inno Setup MyAppVersion' = Get-RegexCapture -Path $innoScriptPath -Pattern '^\s*#define\s+MyAppVersion\s+"([^"]+)"\s*$' -Description 'Inno Setup MyAppVersion'
        'PyInstaller FileVersion' = Get-RegexCapture -Path $versionInfoPath -Pattern "StringStruct\('FileVersion',\s*'([^']+)'\)" -Description 'PyInstaller FileVersion'
        'PyInstaller ProductVersion' = Get-RegexCapture -Path $versionInfoPath -Pattern "StringStruct\('ProductVersion',\s*'([^']+)'\)" -Description 'PyInstaller ProductVersion'
        'PyInstaller filevers tuple' = Get-VersionInfoTuple -Path $versionInfoPath -FieldName 'filevers'
        'PyInstaller prodvers tuple' = Get-VersionInfoTuple -Path $versionInfoPath -FieldName 'prodvers'
    }

    foreach ($declaredVersion in $declaredVersions.GetEnumerator()) {
        $actualNormalized = ConvertTo-FourPartVersion -VersionText $declaredVersion.Value -Description $declaredVersion.Key
        if ($actualNormalized -ne $expectedNormalized) {
            throw "Version mismatch: $($declaredVersion.Key) is '$($declaredVersion.Value)', expected '$ExpectedVersion'."
        }
    }
    Write-Host "==> Version declarations agree on $ExpectedVersion"
}

function Invoke-PackagedSmokeTest {
    param([Parameter(Mandatory = $true)][string]$ApplicationPath)

    Write-Host '==> Smoke-testing the packaged executable'
    $processInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $processInfo.FileName = $ApplicationPath
    $processInfo.Arguments = '--smoke-test'
    $processInfo.UseShellExecute = $false
    $processInfo.CreateNoWindow = $true

    $smokeProcess = [System.Diagnostics.Process]::new()
    $smokeProcess.StartInfo = $processInfo
    if (-not $smokeProcess.Start()) {
        throw 'The packaged executable could not be started.'
    }

    if (-not $smokeProcess.WaitForExit(30000)) {
        $smokeProcess.Kill()
        $smokeProcess.WaitForExit()
        throw 'The packaged smoke test did not finish within 30 seconds.'
    }

    if ($smokeProcess.ExitCode -ne 0) {
        throw "The packaged smoke test failed with exit code $($smokeProcess.ExitCode)."
    }
    $smokeProcess.Dispose()
}

foreach ($requiredPath in @(
    $entryPoint,
    $pyProjectPath,
    $versionModulePath,
    $versionInfoPath,
    $innoScriptPath,
    $iconGeneratorPath,
    $runtimeSvgPath
)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required build input is missing: $requiredPath"
    }
}

$appVersion = Get-RegexCapture -Path $pyProjectPath -Pattern '^version\s*=\s*"([^"]+)"\s*$' -Description 'project version'
Assert-VersionConsistency -ExpectedVersion $appVersion

$runStamp = Get-Date -Format 'yyyyMMdd-HHmmssfff'
$runRoot = Join-Path $artifactRoot $runStamp
$workPath = Join-Path $runRoot 'work'
$specPath = Join-Path $runRoot 'spec'
$distPath = Join-Path $runRoot 'bundle'
$bundlePath = Join-Path $distPath $appName
$executablePath = Join-Path $bundlePath "$appName.exe"
$portableZipPath = Join-Path $runRoot "$appName-$appVersion-win-x64-portable.zip"

if (-not (Test-Path -LiteralPath $buildPython -PathType Leaf)) {
    $pythonLauncher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if (-not $pythonLauncher) {
        throw 'Python Launcher for Windows was not found. Install 64-bit Python 3.13 and try again.'
    }

    Invoke-CheckedCommand -FilePath $pythonLauncher.Source -CommandArguments @(
        '-3.13',
        '-m',
        'venv',
        $buildVenvPath
    ) -Description 'Creating .venv-build313 with Python 3.13'
}

$pythonCheck = & $buildPython -c "import struct, sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) and struct.calcsize('P') == 8 else 1)"
if ($LASTEXITCODE -ne 0) {
    throw '.venv-build313 must use 64-bit Python 3.13. Remove that build environment manually, then rerun this script.'
}

if (-not $SkipDependencyInstall) {
    Invoke-CheckedCommand -FilePath $buildPython -CommandArguments @(
        '-m', 'pip', 'install', '--disable-pip-version-check', '--require-virtualenv',
        'pip==26.2.1', 'setuptools==84.0.0', 'wheel==0.48.0'
    ) -Description 'Updating the pinned build toolchain'
    Invoke-CheckedCommand -FilePath $buildPython -CommandArguments @(
        '-m',
        'pip',
        'install',
        '--disable-pip-version-check',
        '--require-virtualenv',
        '-e',
        "$projectRoot[build,test]"
    ) -Description 'Installing pinned build and test dependencies'
}

Invoke-CheckedCommand -FilePath $buildPython -CommandArguments @(
    $iconGeneratorPath,
    '--source',
    $runtimeSvgPath,
    '--output',
    $iconPath
) -Description 'Generating the multi-resolution Windows icon'

if (-not $SkipTests) {
    Invoke-CheckedCommand -FilePath $buildPython -CommandArguments @(
        '-m',
        'pytest'
    ) -Description 'Running automated tests'
}

New-Item -ItemType Directory -Path $runRoot -ErrorAction Stop | Out-Null

$pyInstallerArguments = @(
    '-m',
    'PyInstaller',
    '--noconfirm',
    '--windowed',
    '--onedir',
    '--noupx',
    '--additional-hooks-dir',
    (Join-Path $projectRoot 'packaging\hooks'),
    '--name',
    $appName,
    '--paths',
    $projectRoot,
    '--version-file',
    $versionInfoPath,
    '--workpath',
    $workPath,
    '--specpath',
    $specPath,
    '--distpath',
    $distPath,
    '--icon',
    $iconPath,
    '--add-data',
    "$runtimeAssetsPath;sequence_renamer\assets"
)

$pyInstallerArguments += $entryPoint

Invoke-CheckedCommand -FilePath $buildPython -CommandArguments $pyInstallerArguments -Description 'Building the standalone Windows bundle'

if (-not (Test-Path -LiteralPath $executablePath -PathType Leaf)) {
    throw "PyInstaller completed without creating the expected executable: $executablePath"
}

Invoke-PackagedSmokeTest -ApplicationPath $executablePath

# Distribute notices next to the executable as well as the bundled font license.
Copy-Item -LiteralPath (Join-Path $projectRoot 'licenses') -Destination $bundlePath -Recurse
$pythonLicense = & $buildPython -c "import pathlib, sys; print(pathlib.Path(sys.base_prefix) / 'LICENSE.txt')"
if (-not (Test-Path -LiteralPath $pythonLicense -PathType Leaf)) {
    throw 'The Python runtime license is missing from the build interpreter.'
}
Copy-Item -LiteralPath $pythonLicense -Destination (Join-Path $bundlePath 'licenses\Python.txt')
Copy-Item -LiteralPath (Join-Path $projectRoot 'docs\third-party.md') -Destination (Join-Path $bundlePath 'THIRD-PARTY-NOTICES.md')
if (Test-Path -LiteralPath (Join-Path $projectRoot 'LICENSE')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot 'LICENSE') -Destination $bundlePath
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'NOTICE') -Destination $bundlePath

Write-Host '==> Creating the portable ZIP'
if (Test-Path -LiteralPath $portableZipPath) {
    throw "Refusing to overwrite an existing portable archive: $portableZipPath"
}
Invoke-CheckedCommand -FilePath $buildPython -CommandArguments @(
    '-m', 'zipfile', '-c', $portableZipPath, $bundlePath
) -Description 'Archiving the portable bundle'

$installerPath = $null
if (-not $SkipInstaller) {
    $resolvedIsccPath = Resolve-InnoCompiler -RequestedPath $IsccPath
    if ($resolvedIsccPath) {
        $installerBaseName = "$appName-Setup-x64"
        Invoke-CheckedCommand -FilePath $resolvedIsccPath -CommandArguments @(
            "/DSourceDir=$bundlePath",
            "/DOutputDir=$runRoot",
            "/DOutputBaseFilename=$installerBaseName",
            "/DMyAppVersion=$appVersion",
            "/DSetupIconPath=$iconPath",
            $innoScriptPath
        ) -Description 'Building the Inno Setup installer'

        $installerPath = Join-Path $runRoot "$installerBaseName.exe"
        if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
            throw "Inno Setup completed without creating the expected installer: $installerPath"
        }
    }
    else {
        throw 'Inno Setup 6 was not found. Install it, pass -IsccPath, or explicitly use -SkipInstaller.'
    }
}

Write-Host ''
Write-Host 'Build completed successfully.'
Write-Host "Standalone bundle: $bundlePath"
Write-Host "Portable ZIP:      $portableZipPath"
if ($installerPath) {
    Write-Host "Installer:         $installerPath"
}
