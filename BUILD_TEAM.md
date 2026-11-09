# Build the Chinese Team Edition 1.0.0

This fork uses upstream 1.3.0 as its baseline. Its independent team version is 1.0.0.
Build on Windows x64 with Python 3.12 and Inno Setup 6.7.4.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-local.lock
.\.venv\Scripts\python.exe -m pip install "setuptools>=77" PyInstaller==6.22.3 pytest==9.2.0
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:QT_QPA_FONTDIR = 'C:\Windows\Fonts'
.\scripts\build_windows.ps1
```

The build synchronizes version metadata from pyproject.toml, runs isolated tests,
builds an onedir application with its icon and translation catalogs, audits the
payload, then builds the Chinese single-EXE installer. No Python installation is
needed on the target computer. The corresponding source can be regenerated with:

```powershell
.\.venv\Scripts\python.exe scripts/create_team_source.py --destination release/DataMaskStudio-Source-1.0.0.zip
```

Use `-SkipSmokeTests` when intentionally building without executing installers.
Use `-SmokeTestsOnly` to validate already-built artifacts. Lifecycle testing refuses
to proceed if this team edition is already registered, to protect existing installs.
It uses temporary installation/data directories and disables shortcuts and autorun.

The opt-in frozen probe checks the real nine-page main window, the icon and catalogs,
the application version, encrypted local mappings, text/Markdown/JSON/Word restoration,
and XLSX import. It exits and writes a structured report instead of treating a living
error-dialog process as a successful startup. Synthetic probe data is removed.

The spec restricts DLL discovery to the active Python environment and Windows.
It keeps compatible MSVC runtime DLLs app-local, rejects unrelated external binaries,
and excludes ICU and Windows CRT/API-set DLLs from the application payload.
Qt in this environment imports Microsoft's unversioned ICU API from Windows;
copying a Poppler ICU DLL with renamed exports such as ucnv_open_78 breaks that ABI.
Do not copy DLLs from unrelated PDF/image tools or developer PATH directories.

References:
- https://doc.qt.io/qt-6.12/windows.html
- https://learn.microsoft.com/en-us/windows/win32/intl/international-components-for-unicode--icu-
- https://jrsoftware.org/isdl.php

The application installer is unsigned unless a maintainer supplies a valid signing
certificate and signing pipeline. No key, vault, report, rules or user preferences
may be added to release artifacts. See NOTICE.txt and the included license texts.
