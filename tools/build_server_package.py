from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


PACKAGE_ITEMS = (
    "server.py",
    "legal_generator.py",
    "run.bat",
    "README.md",
    "使用说明.txt",
    "examples",
    "static",
    "templates",
    "deploy",
)

PACKAGE_EXCLUDES = {
    "templates/source/金融借款合同纠纷民事起诉状-黄色示例.docx",
}


def build_package(destination: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in PACKAGE_ITEMS:
            source = root / relative
            files = [source] if source.is_file() else sorted(path for path in source.rglob("*") if path.is_file())
            for path in files:
                archive_path = path.relative_to(root).as_posix()
                if archive_path in PACKAGE_EXCLUDES:
                    continue
                archive.write(path, archive_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Linux-compatible server deployment ZIP")
    parser.add_argument(
        "destination",
        nargs="?",
        type=Path,
        default=Path("outputs/律书生成台-服务器版0.6.0.zip"),
    )
    args = parser.parse_args()
    build_package(args.destination.resolve())
    print(args.destination.resolve())


if __name__ == "__main__":
    main()
