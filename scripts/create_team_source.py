"""Export corresponding source from explicit public roots, never workspace state."""

import argparse
from pathlib import Path
import tomllib
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {"LICENSE", "NOTICE.txt", "README.md", "README.pt-BR.md", "CHANGELOG.md", "COMPATIBILITY.md", "PRIVACY.md", "SECURITY.md",
              "pyproject.toml", "requirements-local.lock", ".gitignore", "团队版说明.md", "BUILD_TEAM.md"}
TREES = {"src": {".py", ".json"}, "scripts": {".py", ".ps1"}, "packaging": {".py", ".iss", ".spec", ".isl", ".txt"},
         "tests": {".py"}, "benchmarks": {".py"}, "assets": {".ico", ".svg", ".png"},
         ".github": {".yml", ".yaml", ".md"}, "docs": {".html", ".css", ".xml", ".txt", ".svg", ".png"}}
PUBLIC_SCREENSHOTS = {"data-mask-studio-main.png", "data-mask-studio-batch.png", "data-mask-studio-package.png"}
PRIVATE_NAMES = {"profiles.json", "field_rules.json", "preferences.json", "vault.db", "secret.key", "vault_key.dpapi", "direct_url.json"}


def create(destination: Path):
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    paths = [ROOT / name for name in ROOT_FILES if (ROOT / name).is_file()]
    for tree, extensions in TREES.items():
        for path in (ROOT / tree).rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in extensions:
                continue
            relative = path.relative_to(ROOT)
            if "__pycache__" in relative.parts or "security" in relative.parts or ".data" in relative.parts:
                continue
            if tree == "docs" and "images" in relative.parts and path.name not in PUBLIC_SCREENSHOTS:
                continue
            if path.name in PRIVATE_NAMES or path.is_symlink() or not path.resolve().is_relative_to(ROOT):
                raise ValueError("Private or linked source input is not allowed")
            paths.append(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            archive.write(path, f"DataMaskStudio-Source-{version}/{path.relative_to(ROOT).as_posix()}")
    print(f"Exported source {version}: {len(paths)} public files")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", required=True, type=Path)
    create(parser.parse_args().destination)
