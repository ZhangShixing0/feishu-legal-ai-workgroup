from __future__ import annotations

import re
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".bat", ".conf", ".css", ".html", ".ini", ".js", ".json", ".md",
    ".ps1", ".py", ".service", ".sh", ".toml", ".txt", ".yaml", ".yml",
}
RUNTIME_PREFIXES = ("tmp/", "outputs/", "work/", "feishu_workgroup/data/")
CASE_SUFFIXES = {".zip", ".xlsx", ".xls", ".pdf", ".jpg", ".jpeg", ".png", ".wps", ".dat"}


def repository_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [ROOT / item.decode("utf-8", "surrogateescape") for item in result.stdout.split(b"\0") if item]


def valid_cn_id(value: str) -> bool:
    value = value.upper()
    if not re.fullmatch(r"\d{17}[0-9X]", value) or value.startswith("000000"):
        return False
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    checks = "10X98765432"
    return checks[sum(int(value[i]) * weights[i] for i in range(17)) % 11] == value[-1]


def placeholder(value: str) -> bool:
    normalized = value.strip().strip('"\'').lower()
    return (
        not normalized
        or set(normalized) <= {"x", "_", "-"}
        or "example" in normalized
        or "changeme" in normalized
        or normalized.startswith("demo")
        or normalized.startswith("${")
        or normalized.startswith("<")
    )


def inspect_text(path: Path, text: str, problems: list[str]) -> None:
    relative = path.relative_to(ROOT).as_posix()
    for match in re.finditer(r"(?m)^\s*([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD))\s*=\s*(.+?)\s*$", text):
        if not placeholder(match.group(2)):
            problems.append(f"{relative}: 疑似真实密钥配置 {match.group(1)}")
    for match in re.finditer(r"(?<![a-z0-9])cli_[a-z0-9]{16,}(?![a-z0-9])", text, re.I):
        if "x" not in match.group().lower():
            problems.append(f"{relative}: 疑似真实飞书 App ID")
    if any(valid_cn_id(match.group()) for match in re.finditer(r"(?<!\d)\d{17}[0-9Xx](?!\d)", text)):
        problems.append(f"{relative}: 包含校验有效且非 000000 区码的身份证号")
    unsafe_phones = {
        match.group()
        for match in re.finditer(r"(?<!\d)1[3-9]\d{9}(?!\d)", text)
        if not match.group().startswith("199000000")
    }
    if unsafe_phones:
        problems.append(f"{relative}: 包含未列入测试号段的手机号格式数据")


def inspect_docx(path: Path, problems: list[str]) -> None:
    relative = path.relative_to(ROOT).as_posix()
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            document = ET.fromstring(archive.read("word/document.xml"))
            visible = "".join(node.text or "" for node in document.iter() if node.tag.endswith("}t"))
            inspect_text(path, visible, problems)
            if "docProps/custom.xml" in names:
                problems.append(f"{relative}: 仍包含 Word 自定义属性")
            if "docProps/core.xml" in names:
                core = archive.read("docProps/core.xml").decode("utf-8", errors="ignore")
                if re.search(r"<(?:dc:creator|cp:lastModifiedBy)>[^<]+", core):
                    problems.append(f"{relative}: 仍包含 Word 作者元数据")
    except (KeyError, OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        problems.append(f"{relative}: 无法审计 DOCX（{type(exc).__name__}）")


def main() -> int:
    problems: list[str] = []
    files = repository_files()
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        lowered = relative.lower()
        if lowered.startswith(RUNTIME_PREFIXES) and lowered != "outputs/.gitkeep":
            problems.append(f"{relative}: 运行时目录不应进入版本控制")
            continue
        if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.feishu.example"):
            problems.append(f"{relative}: 本地环境文件不应进入版本控制")
        if path.suffix.lower() in CASE_SUFFIXES and not lowered.startswith("docs/assets/"):
            problems.append(f"{relative}: 疑似案件材料或导出文件")
        if path.suffix.lower() == ".docx" and not lowered.startswith("templates/source/"):
            problems.append(f"{relative}: DOCX 仅允许出现在 templates/source/")
        if not path.is_file() or "/vendor/" in f"/{lowered}":
            continue
        if path.suffix.lower() in TEXT_SUFFIXES:
            inspect_text(path, path.read_text(encoding="utf-8-sig", errors="ignore"), problems)
        elif path.suffix.lower() == ".docx":
            inspect_docx(path, problems)

    if problems:
        print("发布安全审计失败：")
        for problem in sorted(set(problems)):
            print(f"- {problem}")
        return 1
    print(f"发布安全审计通过：已检查 {len(files)} 个候选文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
