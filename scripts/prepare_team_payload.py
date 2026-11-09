"""Add only reviewed public instructions, version metadata and runtime licenses."""

import argparse
from importlib.metadata import distribution
from pathlib import Path
import shutil
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ("PySide6", "PySide6_Essentials", "shiboken6", "cryptography", "cffi", "pycparser", "openpyxl", "et_xmlfile", "defusedxml", "pyinstaller")


def prepare(destination: Path):
    destination = destination.resolve()
    if not (destination / "DataMaskStudio.exe").is_file():
        raise ValueError("Expected a built application directory")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    shutil.copyfile(ROOT / "LICENSE", destination / "LICENSE.txt")
    shutil.copyfile(ROOT / "NOTICE.txt", destination / "NOTICE.txt")
    guide = (ROOT / "团队版说明.md").read_text(encoding="utf-8")
    (destination / "团队使用说明.txt").write_text(guide, encoding="utf-8-sig")
    license_root = destination / "licenses"
    license_root.mkdir(exist_ok=True)
    for license_file in (ROOT / "packaging/windows/licenses").glob("*.txt"):
        shutil.copyfile(license_file, license_root / license_file.name)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise ValueError("Python license is required")
    shutil.copyfile(python_license, license_root / "Python-LICENSE.txt")
    rows = [f"Data Mask Studio Chinese Team Edition {version}", "", "Runtime components and PyInstaller bootloader licenses:",
            "Qt / PySide6: Copyright (C) The Qt Company Ltd. and contributors.",
            "See licenses/LGPL-3.0-only.txt and licenses/GPL-3.0-only.txt.",
            "Qt sources: https://code.qt.io/cgit/qt/qtbase.git/",
            "PySide sources: https://code.qt.io/cgit/pyside/pyside-setup.git/", ""]
    for name in RUNTIME:
        package = distribution(name)
        rows.append(f"{package.metadata['Name']} {package.version}")
        rows.append("License: " + (package.metadata.get("License-Expression") or package.metadata.get("License") or "See package notices"))
        for link in package.metadata.get_all("Project-URL") or ():
            rows.append(link)
        for relative in package.files or ():
            if not any(word in str(relative).lower() for word in ("license", "licence", "copying", "notice")):
                continue
            source = Path(package.locate_file(relative))
            if not source.is_file():
                continue
            target = license_root / name / source.name
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, target)
        rows.append("")
    rows.append("Python " + sys.version.split()[0] + "; see licenses/Python-LICENSE.txt")
    (destination / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (destination / "VERSION.txt").write_text(version + "\n", encoding="ascii")
    print(f"Prepared public team payload {version}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", required=True, type=Path)
    prepare(parser.parse_args().destination)
