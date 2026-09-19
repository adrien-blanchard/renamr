# Renamr

Renamr is a minimal Windows desktop application for safely renaming PNG and EXR image sequences. It preserves every parsed numeric frame ID and gap while the output token controls padding, and previews the complete operation before changing anything on disk.

## Key behavior

- The frame number comes from each source filename, never from the file selection order.
- Missing frames stay missing. A sequence containing frames `1001`, `1002`, and `1004` will not create or renumber frame `1003`.
- Import and preview order may be retained for convenience, but every source-to-destination mapping uses the parsed source frame ID.
- Existing destinations, duplicate destinations, invalid Windows names, ambiguous frame numbers, and inaccessible files are reported before renaming. Ambiguous frame detection blocks both **Rename Sequence** and **Find & Replace**.
- **Rename Sequence** applies one global output pattern, so combining distinct sequences in one operation is blocked to prevent accidental merging. Process each sequence separately.
- Rename and Undo run asynchronously. Each rename uses temporary names, an on-disk journal, rollback protection, and startup recovery so swaps, cycles, and interrupted operations cannot silently overwrite files.
- PNG and EXR contents are never decoded or modified; only filenames change.
- Only one application instance can run at a time, preventing two windows from acting on the same recovery journal.
- The last successfully used folder is remembered in `%LOCALAPPDATA%\Renamr\settings.json`; it is not based on the application's launch directory. If unavailable, the application falls back to the Windows Pictures folder.

## Frame tokens

The output pattern accepts common compositing notation:

| Pattern | Meaning | Example output for frame 7 |
| --- | --- | --- |
| `####` | Four-digit frame token | `shot.0007.exr` |
| `######` | Six-digit frame token | `shot.000007.exr` |
| `%04d` | Nuke/printf four-digit token | `shot.0007.exr` |
| `%d` | Frame number with no minimum padding | `shot.7.exr` |
| `%04` | Convenience alias for `%04d` | `shot.0007.exr` |

For example:

```text
Source files:
plate_v003.1001.exr
plate_v003.1002.exr
plate_v003.1004.exr

Output pattern:
comp_v004.####.exr

Result:
comp_v004.1001.exr
comp_v004.1002.exr
comp_v004.1004.exr
```

`v003` remains a version number and the final numeric field is treated as the frame number. When a filename is genuinely ambiguous, Renamr blocks the operation instead of guessing.

## Install on another PC

The recommended release is `Renamr-Setup-x64.exe`:

1. Copy the installer to a 64-bit PC running Windows 10 version 1809 or later.
2. Run it and follow the installation wizard.
3. Launch **Renamr** from the Start menu.

The target PC does not need Python, PySide6, or PyInstaller. Installation is per user and does not normally require administrator rights.

A portable ZIP is generated with every build. It intentionally contains a PyInstaller `onedir` bundle for reliable startup and faster launch. Extract the complete `Renamr` folder before running `Renamr.exe`; do not move the executable away from the bundled files.

## Use

1. Drop files or folders onto the import area, or click it to browse.
2. Select **Rename Sequence** or **Find & Replace**.
3. Enter the output pattern and inspect every row in the preview.
4. Resolve any reported conflict or ambiguous frame number.
5. Select **Rename Files** and confirm the operation.

Drag and drop only imports files. It never changes frame IDs, and rows cannot be reordered by dragging.

Only rename completed sequences that are not being written by a renderer, compositor, sync client, or copy process.

Renamr stores user settings under `%LOCALAPPDATA%\Renamr`. A valid setting from an earlier Sequence Renamer installation is imported once, but it never replaces an existing Renamr setting. The application stores the last folder path, not the previous file selection.

For upgrade safety, diagnostic logs, transaction journals, and the single-instance lock retain the legacy internal `%LOCALAPPDATA%\SequenceRenamer` state directory. This preserves interrupted-operation recovery and Undo history, and prevents old and new builds from running concurrently. Journals contain only the filename mappings and metadata required for recovery and Undo.

## Development setup

Python 3.13 x64 is required. From PowerShell at the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe batch_renamer.py
```

The runtime, test, and packaging dependencies are pinned in `pyproject.toml` to make local and release builds consistent.

## Test

Run the complete automated test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Run the non-interactive application smoke test:

```powershell
.\.venv\Scripts\python.exe batch_renamer.py --smoke-test
```

Tests must use temporary directories. Do not point automated tests at production footage.

## Build the Windows release

The build script creates and reuses `.venv-build313` in the repository, runs the tests, builds a PyInstaller standalone `onedir` bundle, smoke-tests the packaged executable, creates a portable ZIP, and builds the installer with Inno Setup. Inno Setup is required by default; the build fails if `ISCC.exe` is unavailable unless `-SkipInstaller` is specified.

```powershell
PowerShell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

Useful options:

```powershell
# Reuse dependencies already installed in .venv-build313
.\scripts\build_windows.ps1 -SkipDependencyInstall

# Build without running pytest (not recommended for a release)
.\scripts\build_windows.ps1 -SkipTests

# Build only the portable bundle and skip the installer
.\scripts\build_windows.ps1 -SkipInstaller

# Use a specific Inno Setup compiler
.\scripts\build_windows.ps1 -IsccPath "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

Each run writes to a new timestamped directory under `artifacts`; existing releases are not deleted or overwritten. Expected outputs include:

```text
artifacts/<timestamp>/bundle/Renamr/Renamr.exe
artifacts/<timestamp>/Renamr-<version>-win-x64-portable.zip
artifacts/<timestamp>/Renamr-Setup-x64.exe
```

The build must run on Windows because PyInstaller does not cross-compile Windows executables from macOS or Linux.

Release artifacts are unsigned unless a code-signing certificate and signing step are added to the release process. Windows may therefore show an unknown-publisher or SmartScreen warning.
