"""Keep the standard Qt hook, without unused PDF and virtual-keyboard plugins."""

from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
binaries = [
    (source, destination)
    for source, destination in binaries
    if Path(source).stem.lower() not in {"qpdf", "qtvirtualkeyboardplugin"}
]
