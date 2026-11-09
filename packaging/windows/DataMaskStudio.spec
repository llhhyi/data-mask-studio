import os
import importlib.util
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

project_root = Path(SPECPATH).resolve().parents[1]
source_root = project_root / "src"
entry_point = source_root / "data_mask_studio" / "__main__.py"
version_file = os.environ.get("DMS_VERSION_FILE")
icon_value = os.environ.get("DMS_ICON_FILE")
if not icon_value:
    raise RuntimeError("DMS_ICON_FILE deve apontar para o ícone oficial obrigatório.")
icon_path = Path(icon_value)
if not icon_path.is_file():
    raise FileNotFoundError(f"Ícone oficial obrigatório não encontrado: {icon_path}")

datas = [(str(icon_path), "assets/branding")]
datas += collect_data_files("data_mask_studio.i18n", includes=["catalogs/*.json"])
datas += copy_metadata("data-mask-studio")

# Keep a consistent app-local MSVC runtime ahead of older system/PATH copies.
qt_directory = Path(importlib.util.find_spec("PySide6").origin).parent
runtime_binaries = [(str(path), ".") for path in qt_directory.glob("*.dll")
                    if path.name.lower().startswith(("msvcp140", "vcruntime140"))]

# Build hosts may have image/PDF tooling on PATH with incompatible CRT shims.
windows_root = Path(os.environ["WINDIR"])
os.environ["PATH"] = os.pathsep.join(map(str, (
    Path(sys.executable).parent, Path(sys.base_prefix), windows_root / "System32", windows_root,
)))

analysis = Analysis(
    [str(entry_point)],
    pathex=[str(source_root)],
    binaries=runtime_binaries,
    datas=datas,
    hiddenimports=["data_mask_studio.i18n"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project_root / "packaging/windows/runtime_smoke.py")],
    excludes=["pytest", "unittest", "tkinter", "tests"],
    noarchive=False,
    optimize=1,
)

# Metadado criado por instalações editáveis; pode revelar o caminho local do
# repositório e não é necessário para consultar a versão do pacote.
analysis.datas = [
    item for item in analysis.datas if Path(item[0]).name != "direct_url.json"
]

# Qt uses Windows' unversioned ICU ABI; a Poppler ICU DLL has renamed symbols.
analysis.binaries = [item for item in analysis.binaries
                     if not Path(item[0]).name.lower().startswith(("api-ms-win-", "ext-ms-win-", "icu"))
                     and Path(item[0]).name.lower() != "ucrtbase.dll"]
allowed_binary_roots = (qt_directory.parent.resolve(), Path(sys.base_prefix).resolve(), windows_root.resolve())
for _destination, _source, _kind in analysis.binaries:
    if not any(Path(_source).resolve().is_relative_to(root) for root in allowed_binary_roots):
        raise RuntimeError("Unexpected external DLL in build: " + Path(_source).name)

python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="DataMaskStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path),
    version=version_file,
)

portable = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DataMaskStudio",
)
