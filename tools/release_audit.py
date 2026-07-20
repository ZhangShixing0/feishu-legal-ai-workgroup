from __future__ import annotations

import hashlib
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
FORBIDDEN_PII_SHA256 = {
    "f61683ae4b1da523e5df4fbadd7a02fb22d266d2c717cb6552da42d7a1038676",
    "e0f8aebbf712178d2fb75c45ec01a24d1c457c5a49bed45ff1a5ef320b50a3e7",
    "1d841bc0ee98309cb7916670b7f0fdef5f4c35150711a41405ef3633b56322cf",
    "1da1199765ccf6d094c4e0ec9e2c889cabc527515bda82ed1b34c5ea01f3b0e9",
    "81ef90a748aba007bb6159e012274ef83c1e4e8c348b38298006adde143f77c1",
    "5bcbe250e9effac0e2768828200b2767e5c494f15101a6c95cee420dc8b3dddc",
    "8159c9a504fc0b9a7307677d6cde84b36d904646168ec318c7bc3fe8592fb297",
    "bd5ce34afeca90b72b1e7aa5ef5d894bd7fd3d942cf27189ee0a6d7a29fe67e4",
    "64424ca1e3ee7248bf5685b8a9d70b52be706b0d00507c6e1e553b613607f7c1",
}
TEST_ENTITY_MARKERS = ("测试", "示例", "虚构", "demo")
ENTITY_FIELDS = (
    "defendant_name", "loan_borrower", "plaintiff_name", "original_creditor",
    "loan_lender", "lending_org_full", "defendant_employer", "relationship_manager",
)


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


def forbidden_pii_hash_hit(text: str) -> bool:
    spans = [
        (span, 2) for span in re.findall(r"[\u4e00-\u9fff·]{2,}", text)
    ] + [
        (span, 3) for span in re.findall(r"[A-Za-z0-9_-]{3,}", text)
    ]
    for span, minimum in spans:
        maximum = min(24, len(span))
        for width in range(minimum, maximum + 1):
            for start in range(0, len(span) - width + 1):
                digest = hashlib.sha256(span[start:start + width].encode("utf-8")).hexdigest()
                if digest in FORBIDDEN_PII_SHA256:
                    return True
    return False


def inspect_text(path: Path, text: str, problems: list[str]) -> None:
    relative = path.relative_to(ROOT).as_posix()
    if forbidden_pii_hash_hit(text):
        problems.append(f"{relative}: 包含禁止发布的真实或含混主体标识（哈希命中）")
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
    entity_pattern = re.compile(
        rf"(?:\[['\"](?:{'|'.join(ENTITY_FIELDS)})['\"]\]|\b(?:{'|'.join(ENTITY_FIELDS)})\b)"
        r"\s*[:=]\s*['\"]([^'\"]*)['\"]",
        re.I,
    )
    for match in entity_pattern.finditer(text):
        value = match.group(1).strip()
        if value and not any(marker in value.lower() for marker in TEST_ENTITY_MARKERS):
            problems.append(f"{relative}: 示例主体字段未明确标注为测试数据：{value}")
    for match in re.finditer(r"(?:被告姓名|借款人姓名|客户经理)[：:]\s*['\"“]?([\u4e00-\u9fff·]{2,8})", text):
        value = match.group(1)
        if not any(marker in value.lower() for marker in TEST_ENTITY_MARKERS):
            problems.append(f"{relative}: 姓名字段未明确标注为测试数据：{value}")


def inspect_docx(path: Path, problems: list[str]) -> None:
    relative = path.relative_to(ROOT).as_posix()
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            for name in names:
                if not name.lower().endswith((".xml", ".rels")):
                    continue
                raw = archive.read(name).decode("utf-8", errors="ignore")
                inspect_text(path, raw, problems)
                if name.lower().endswith(".xml"):
                    root = ET.fromstring(raw)
                    visible = "".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
                    inspect_text(path, visible, problems)
            if any(name.startswith(("word/embeddings/", "word/media/")) for name in names):
                problems.append(f"{relative}: DOCX 包含无法用文本规则审计的嵌入对象或媒体")
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
        inspect_text(path, relative, problems)
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
