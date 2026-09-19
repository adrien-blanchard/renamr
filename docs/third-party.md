# Third-party notices

Renamr uses these unmodified third-party components. Their licenses are independent of the application source license.

- **Qt / PySide6 / Shiboken 6.11.1:** LGPL-3.0 components used as dynamically loaded libraries. Copyright The Qt Company and contributors. [Qt for Python source](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.1-src/). [Qt source](https://download.qt.io/official_releases/qt/6.11/6.11.1/single/). License copies are provided under `licenses/`.
- **Inter 4.1:** Copyright the Inter Project Authors; SIL Open Font License 1.1. The full notice is bundled in `sequence_renamer/assets/fonts/Inter-4.1/LICENSE.txt`.
- **Python 3.11:** Python Software Foundation License; retained in the portable runtime.
- **PyInstaller:** GPL with its bootloader exception, which permits distributing the generated application under its own license. [Project license and exception](https://pyinstaller.org/en/stable/license.html).

The portable distribution keeps Qt libraries separate from `Renamr.exe`; it does not use a single-file encrypted bundle. Nothing in the application terms restricts replacing those libraries or reverse engineering as needed to debug modifications to LGPL components. Equivalent source can be used to rebuild the application using the documented build script.

Keep this notice, the font license and the library license texts when redistributing a build. Commercial Qt licensing is not claimed.
