from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Iterable


BASE_DIR = Path(__file__).resolve().parent
VENDOR_DIR = BASE_DIR / "vendor"
if str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - surfaced in the UI as a warning
    PdfReader = None


HEADERS = [
    "序号", "客户编号", "贷款合同编号", "贷款账号", "未偿本金余额", "未偿利息余额", "合计",
    "借款人姓名", "借款人出生日期", "借款人年龄", "借款人教育程度", "职业",
    "发放贷款时借款人所在单位性质", "借款人年收入", "婚姻状态", "性别", "借款合同约定送达地址",
    "借款人身份证号码", "借款人身份证所在省", "借款人身份证所在市", "借款人身份证地址",
    "借款人手机号", "一级分行", "二级分行", "经办行", "分行所在省份", "分行所在城市",
    "产品类别", "贷款种类", "担保方式", "还款来源", "还款方式", "授信额度（元）", "贷款金额",
    "币种", "线上线下", "截至基准日执行利率", "贷款利率类型", "贷款合同期限（月）", "贷款剩余期数",
    "贷款发放日", "贷款到期日", "贷款投向行业", "基准日逾期天数", "基准日进入不良的天数",
    "最后一次还款时间", "最后一次还款金额", "未偿本金余额", "未偿利息余额", "未偿费用",
    "基准日五级分类", "是否本行员工", "最后一次不良的时间", "进入不良时未偿本金余额",
    "进入不良时未偿利息余额", "进入不良时未偿费用余额", "进入不良至基准日累计已偿还本金金额",
    "是否委外催收", "委外催收机构", "是否起诉", "该笔贷款目前所处法律程序阶段", "是否涉刑",
    "客户经理", "贷款发放机构全称", "贷款发放机构简称", "贷款发放机构统一社会信用代码",
    "贷款发放机构地区", "贷款发放机构所属总行的统一社会信用代码",
]


NUMERIC_HEADERS = {
    "未偿本金余额", "未偿利息余额", "合计", "借款人年龄", "借款人年收入", "授信额度（元）",
    "贷款金额", "截至基准日执行利率", "贷款合同期限（月）", "贷款剩余期数", "基准日逾期天数",
    "基准日进入不良的天数", "最后一次还款金额", "未偿费用", "进入不良时未偿本金余额",
    "进入不良时未偿利息余额", "进入不良时未偿费用余额", "进入不良至基准日累计已偿还本金金额",
}


PROVINCES = [
    "北京市", "天津市", "上海市", "重庆市", "河北省", "山西省", "辽宁省", "吉林省", "黑龙江省",
    "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省", "河南省", "湖北省", "湖南省",
    "广东省", "海南省", "四川省", "贵州省", "云南省", "陕西省", "甘肃省", "青海省", "台湾省",
    "内蒙古自治区", "广西壮族自治区", "西藏自治区", "宁夏回族自治区", "新疆维吾尔自治区",
    "香港特别行政区", "澳门特别行政区",
]

# 仓库内只保留虚构机构名称。正式环境如需规范化真实机构简称，请仅在未提交的
# 本地环境变量中设置这两个值，避免客户或合作机构名称进入版本库。
BANK_FULL_NAME = os.environ.get("MATERIAL_BANK_FULL_NAME", "示例银行股份有限公司").strip()
BANK_SHORT_NAME = os.environ.get("MATERIAL_BANK_SHORT_NAME", "示例银行").strip()
LOAN_PRODUCT_KEYWORD = os.environ.get("MATERIAL_LOAN_PRODUCT_KEYWORD", "示例贷").strip()
LOAN_PRODUCT_CODE = os.environ.get("MATERIAL_LOAN_PRODUCT_CODE", "DEMO-LOAN").strip()


@dataclass
class SourceDocument:
    path: str
    kind: str
    text: str
    method: str


@dataclass(frozen=True)
class CaseMarker:
    sequence: str
    name: str
    token: str

    @property
    def key(self) -> tuple[str, str, str]:
        return self.sequence, self.name, self.token

    def public(self) -> dict:
        result = {"sequence": self.sequence, "name": self.name, "token": self.token}
        if len(self.token) >= 22:
            result["account"] = self.token
        else:
            result["contract"] = self.token
        return result


@dataclass
class FieldResult:
    value: str | int | float
    source: str
    confidence: float
    method: str
    missing: bool = False

    def public(self, header: str, index: int) -> dict:
        return {
            "index": index,
            "column": column_letter(index + 1),
            "header": header,
            "value": self.value,
            "source": self.source,
            "confidence": round(self.confidence, 2),
            "method": self.method,
            "missing": self.missing,
        }


def column_letter(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


CASE_PATH_PATTERN = re.compile(
    r"(?:^|[\\/])(\d{1,8})-([^-\\/]{2,30})-([0-9A-Za-z]{16,40})(?=[-\\/]|$)"
)


def case_marker_from_path(path: str) -> CaseMarker | None:
    match = CASE_PATH_PATTERN.search("\\" + path)
    if not match:
        return None
    return CaseMarker(match.group(1), clean_value(match.group(2)), normalize_ocr_digits(match.group(3)))


def _case_matches_hint(marker: CaseMarker, hint: dict | None) -> bool:
    if not hint:
        return False
    groups = [
        (clean_value(marker.sequence), [hint.get("sequence"), hint.get("序号")]),
        (clean_value(marker.name), [hint.get("name"), hint.get("借款人姓名")]),
        (clean_value(marker.token), [hint.get("token"), hint.get("贷款账号"), hint.get("贷款合同编号")]),
    ]
    comparisons = []
    for actual, raw_values in groups:
        expected_values = {clean_value(str(value)) for value in raw_values if clean_value(str(value or ""))}
        if expected_values:
            comparisons.append(actual in expected_values)
    return bool(comparisons) and all(comparisons)


def select_case_documents(
    documents: list[SourceDocument],
    zip_name: str,
    case_hint: dict | None = None,
) -> tuple[list[SourceDocument], dict | None, list[str]]:
    markers: dict[tuple[str, str, str], CaseMarker] = {}
    for doc in documents:
        marker = case_marker_from_path(doc.path)
        if marker:
            markers[marker.key] = marker
    zip_marker = case_marker_from_path(zip_name)
    if zip_marker:
        markers[zip_marker.key] = zip_marker

    detected = list(markers.values())
    warnings: list[str] = []
    selected = None
    if len(detected) == 1:
        selected = detected[0]
    elif len(detected) > 1:
        matches = [marker for marker in detected if _case_matches_hint(marker, case_hint)]
        if len(matches) != 1:
            labels = "、".join(f"{item.sequence}-{item.name}" for item in detected[:8])
            raise ValueError(
                f"压缩包中检测到 {len(detected)} 个案件（{labels}），但无法根据Excel文件名和行内标识唯一确定目标案件。"
                "请使用“序号XXX.xlsx”命名Excel，或改为上传单个案件材料包。"
            )
        selected = matches[0]
        warnings.append(
            f"检测到 {len(detected)} 个案件，已按Excel目标行选择 {selected.sequence}-{selected.name}；"
            f"其余 {len(detected) - 1} 个案件未参与本次提取。"
        )

    if selected is None:
        return documents, None, warnings

    filtered = []
    for doc in documents:
        marker = case_marker_from_path(doc.path)
        if marker is None or marker.key == selected.key:
            filtered.append(doc)
    return filtered, selected.public(), warnings


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return text.replace("\u3000", " ").replace("\x00", "")


def compact(text: str) -> str:
    return re.sub(r"\s+", "", normalize_text(text))


def clean_value(value: str) -> str:
    value = normalize_text(value)
    value = re.sub(r"^[：:，,。;；/]+|[：:，,。;；/]+$", "", value)
    return re.sub(r"\s+", " ", value).strip()


def safe_extract_zip(zip_path: Path, destination: Path) -> list[Path]:
    destination = destination.resolve()
    extracted: list[Path] = []
    total_size = 0
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) > 500:
            raise ValueError("压缩包文件数量超过 500 个，请拆分后重试。")
        for member in members:
            normalized = member.filename.replace("\\", "/")
            if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
                raise ValueError(f"压缩包含不安全路径：{member.filename}")
            parts = [part for part in normalized.split("/") if part not in ("", ".")]
            if ".." in parts:
                raise ValueError(f"压缩包含不安全路径：{member.filename}")
            total_size += member.file_size
            if member.file_size > 250 * 1024 * 1024 or total_size > 1024 * 1024 * 1024:
                raise ValueError("压缩包解压后过大，请拆分后重试。")
            target = destination.joinpath(*parts).resolve()
            if destination not in target.parents and target != destination:
                raise ValueError(f"压缩包含越界路径：{member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
            extracted.append(target)
    return extracted


def read_text_file(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030", "utf-16", "utf-16le"):
        try:
            return data.decode(encoding)
        except UnicodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_docx(path: Path) -> tuple[str, list[tuple[str, bytes]]]:
    texts: list[str] = []
    images: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            lower = name.lower()
            if lower.startswith("word/") and lower.endswith(".xml"):
                xml = archive.read(name).decode("utf-8", errors="ignore")
                texts.extend(re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", xml, flags=re.S))
            if lower.startswith("word/media/") and lower.endswith((".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")):
                images.append((Path(name).name, archive.read(name)))
    text = "\n".join(re.sub(r"<[^>]+>", "", item) for item in texts)
    return text, images


def extract_binary_strings(path: Path) -> str:
    data = path.read_bytes()
    candidates: list[str] = []
    for encoding in ("utf-16le", "gb18030"):
        decoded = data.decode(encoding, errors="ignore")
        candidates.extend(re.findall(r"[\u4e00-\u9fffA-Za-z0-9，。；：、（）()\-_/]{4,}", decoded))
    return "\n".join(candidates[:1000])


def extract_pdf_text(path: Path) -> tuple[str, int]:
    if PdfReader is None:
        return "", 0
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n\f\n".join(pages), len(pages)


def windows_ocr(path: Path, ocr_script: Path, thorough: bool = False) -> str:
    command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ocr_script),
        "-InputPath", str(path),
    ]
    if thorough:
        command.append("-Thorough")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        command,
        capture_output=True,
        check=False,
        timeout=120,
        creationflags=creation_flags,
    )
    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    payload_line = next((line for line in reversed(stdout.splitlines()) if line.lstrip().startswith("{")), "")
    if completed.returncode != 0 or not payload_line:
        detail = clean_value(stderr or stdout)[:300]
        raise RuntimeError(detail or "Windows OCR 未返回结果")
    payload = json.loads(payload_line)
    return "\n".join(page.get("text", "") for page in payload.get("pages", []))


def _run_tesseract(
    path: Path,
    thorough: bool = False,
    progress: Callable[[str], None] | None = None,
) -> str:
    executable = shutil.which("tesseract")
    if not executable:
        raise RuntimeError("服务器未安装 Tesseract OCR")
    language = os.environ.get("CASE_EXTRACTOR_TESSERACT_LANG", "chi_sim+eng")
    page_modes = ("6", "11") if thorough else ("6",)
    results: list[str] = []
    errors: list[str] = []
    for mode_index, page_mode in enumerate(page_modes, start=1):
        if progress:
            progress(f"OCR识别 {mode_index}/{len(page_modes)}")
        completed = subprocess.run(
            [executable, str(path), "stdout", "-l", language, "--psm", page_mode],
            capture_output=True,
            check=False,
            timeout=120,
        )
        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")
        if completed.returncode == 0 and stdout.strip():
            results.append(stdout)
        elif stderr.strip():
            errors.append(clean_value(stderr)[:300])
    if not results:
        raise RuntimeError("；".join(errors) or "Tesseract OCR 未返回结果")
    unique_lines: list[str] = []
    seen: set[str] = set()
    for result in results:
        for line in result.splitlines():
            normalized = line.strip()
            if normalized and normalized not in seen:
                unique_lines.append(normalized)
                seen.add(normalized)
    return "\n".join(unique_lines)


def tesseract_ocr(
    path: Path,
    thorough: bool = False,
    progress: Callable[[str], None] | None = None,
) -> str:
    if path.suffix.lower() != ".pdf":
        return _run_tesseract(path, thorough, progress)
    renderer = shutil.which("pdftoppm")
    if not renderer:
        raise RuntimeError("服务器未安装 pdftoppm，无法识别扫描版 PDF")
    max_pages = max(1, int(os.environ.get("CASE_EXTRACTOR_OCR_MAX_PAGES", "30")))
    with tempfile.TemporaryDirectory(prefix="case-ocr-") as temp_dir:
        prefix = Path(temp_dir) / "page"
        completed = subprocess.run(
            [renderer, "-r", "200", "-png", "-f", "1", "-l", str(max_pages), str(path), str(prefix)],
            capture_output=True,
            check=False,
            timeout=180,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(clean_value(stderr)[:300] or "PDF 转图片失败")
        pages = sorted(Path(temp_dir).glob("page-*.png"))
        page_texts = []
        for page_index, page in enumerate(pages, start=1):
            if progress:
                progress(f"OCR页 {page_index}/{len(pages)}")
            page_texts.append(_run_tesseract(page, thorough=False))
    return "\n\f\n".join(page_texts)


def offline_ocr(
    path: Path,
    ocr_script: Path,
    thorough: bool = False,
    progress: Callable[[str], None] | None = None,
) -> tuple[str, str]:
    backend = os.environ.get("CASE_EXTRACTOR_OCR_BACKEND", "auto").strip().lower()
    if backend == "none":
        raise RuntimeError("服务器配置已关闭 OCR")
    if backend == "windows" or (backend == "auto" and os.name == "nt"):
        if progress:
            progress("Windows离线OCR")
        return windows_ocr(path, ocr_script, thorough), "Windows离线OCR"
    if backend in {"auto", "tesseract"}:
        return tesseract_ocr(path, thorough, progress), "Tesseract离线OCR"
    raise RuntimeError(f"不支持的 OCR 后端：{backend}")


def collect_documents(
    root: Path,
    ocr_script: Path,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[SourceDocument], list[str]]:
    documents: list[SourceDocument] = []
    warnings: list[str] = []
    files = sorted(path for path in root.rglob("*") if path.is_file())
    image_extensions = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
    for index, path in enumerate(files, start=1):
        relative = str(path.relative_to(root))
        suffix = path.suffix.lower()
        def ocr_progress(detail: str) -> None:
            if progress:
                progress(f"正在读取 {index}/{len(files)}：{path.name}；{detail}")
        if progress:
            progress(f"正在读取 {index}/{len(files)}：{path.name}")
        try:
            if suffix == ".txt":
                documents.append(SourceDocument(relative, "text", read_text_file(path), "文本读取"))
            elif suffix == ".pdf":
                text, pages = extract_pdf_text(path)
                if text.strip():
                    documents.append(SourceDocument(relative, "pdf", text, "PDF文本层"))
                if len(compact(text)) < max(80, pages * 30):
                    ocr_text, ocr_method = offline_ocr(
                        path, ocr_script, thorough=False, progress=ocr_progress
                    )
                    if ocr_text.strip():
                        documents.append(SourceDocument(relative, "pdf-ocr", ocr_text, ocr_method))
            elif suffix == ".docx":
                text, images = extract_docx(path)
                if text.strip():
                    documents.append(SourceDocument(relative, "docx", text, "Word文本层"))
                for image_index, (name, data) in enumerate(images, start=1):
                    temp_image = path.parent / f".__ocr_{path.stem}_{image_index}{Path(name).suffix}"
                    try:
                        temp_image.write_bytes(data)
                        ocr_text, ocr_method = offline_ocr(
                            temp_image, ocr_script, thorough=False, progress=ocr_progress
                        )
                        if ocr_text.strip():
                            documents.append(SourceDocument(f"{relative}::{name}", "docx-image", ocr_text, f"Word图片{ocr_method}"))
                    finally:
                        temp_image.unlink(missing_ok=True)
            elif suffix in image_extensions:
                thorough = any(keyword in path.name for keyword in ("身份", "账户", "对账", "流向", "日志", "交易"))
                text, ocr_method = offline_ocr(
                    path, ocr_script, thorough=thorough, progress=ocr_progress
                )
                if text.strip():
                    documents.append(SourceDocument(relative, "image", text, ocr_method))
            elif suffix in {".wps", ".doc"}:
                text = extract_binary_strings(path)
                if text.strip():
                    documents.append(SourceDocument(relative, "binary-document", text, "文档可见字符串"))
        except Exception as exc:
            warnings.append(f"{relative}：{clean_value(str(exc)) or '读取失败'}")
    documents.extend(SourceDocument(str(path.relative_to(root)), "path", path.name, "材料路径") for path in files)
    return documents, warnings


def valid_cn_id(candidate: str) -> bool:
    candidate = candidate.upper()
    if not re.fullmatch(r"\d{17}[0-9X]", candidate):
        return False
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    checks = "10X98765432"
    return checks[sum(int(candidate[i]) * weights[i] for i in range(17)) % 11] == candidate[-1]


def id_details(identity: str, as_of: date | None = None) -> tuple[str, int, str]:
    as_of = as_of or date.today()
    birthday = date(int(identity[6:10]), int(identity[10:12]), int(identity[12:14]))
    age = as_of.year - birthday.year - ((as_of.month, as_of.day) < (birthday.month, birthday.day))
    gender = "男" if int(identity[16]) % 2 else "女"
    return birthday.isoformat(), age, gender


def normalize_ocr_digits(value: str) -> str:
    table = str.maketrans({
        "O": "0", "o": "0", "D": "0", "Q": "0", "〇": "0", "○": "0", "口": "0",
        "I": "1", "l": "1", "|": "1", "L": "1", "Z": "2", "S": "5", "s": "5", "B": "8",
    })
    return re.sub(r"[^0-9Xx]", "", normalize_text(value).translate(table))


def date_string(year: str, month: str, day: str) -> str:
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "壹": 1, "二": 2, "贰": 2, "两": 2, "三": 3, "叁": 3,
             "四": 4, "肆": 4, "五": 5, "伍": 5, "六": 6, "陆": 6, "七": 7, "柒": 7,
             "八": 8, "捌": 8, "九": 9, "玖": 9}
CN_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000, "万": 10000, "亿": 100000000}


def chinese_integer(text: str) -> int | None:
    total = 0
    section = 0
    number = 0
    seen = False
    for char in text:
        if char in CN_DIGITS:
            number = CN_DIGITS[char]
            seen = True
        elif char in CN_UNITS:
            seen = True
            unit = CN_UNITS[char]
            if unit < 10000:
                section += (number or 1) * unit
            else:
                section = (section + number) * unit
                total += section
                section = 0
            number = 0
    return total + section + number if seen else None


def province_city(address: str) -> tuple[str | None, str | None]:
    compact_address = compact(address)
    province = next((name for name in PROVINCES if name in compact_address), None)
    city = None
    if province:
        tail = compact_address.split(province, 1)[1]
        match = re.match(r"([^省市区县]{1,12}市)", tail)
        if match:
            city = match.group(1)
        elif province.endswith("市"):
            city = province
    return province, city


def month_difference(start: str, end: str) -> int | None:
    try:
        sy, sm, sd = map(int, start.split("-"))
        ey, em, ed = map(int, end.split("-"))
        months = (ey - sy) * 12 + em - sm
        if ed < sd:
            months -= 1
        return months
    except Exception:
        return None


def parse_money(value: str) -> int | float | None:
    normalized = normalize_text(value).replace(",", "").replace("￥", "").replace("¥", "")
    match = re.search(r"-?\d+(?:\.\d+)?", normalized)
    if not match:
        return None
    number = float(match.group(0))
    return int(number) if number.is_integer() else round(number, 2)


def detect_reference_date(text: str) -> date | None:
    candidates: list[date] = []
    for match in re.finditer(
        r"(?:债权基准日|基准日|截至)(?:为|是|：|:)?(20\d{2})[年/.-](\d{1,2})[月/.-](\d{1,2})日?",
        text,
    ):
        try:
            candidates.append(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            continue
    return max(candidates) if candidates else None


def analyze_fields(
    documents: list[SourceDocument],
    zip_name: str,
    case_info: dict | None = None,
) -> list[dict]:
    fields: dict[str, FieldResult] = {}

    def set_field(header: str, value, source: str, confidence: float, method: str) -> None:
        if value is None:
            return
        if isinstance(value, str):
            value = clean_value(value)
            if not value:
                return
        current = fields.get(header)
        if current is None or confidence > current.confidence:
            fields[header] = FieldResult(value, source, confidence, method, False)

    text_documents = [doc for doc in documents if doc.kind != "path" and doc.text.strip()]
    path_documents = [doc for doc in documents if doc.kind == "path"]
    preferred = sorted(text_documents, key=lambda item: (item.method not in {"PDF文本层", "文本读取"}, item.path))
    all_compact = "\n".join(compact(doc.text) for doc in preferred)
    all_paths = "\n".join(doc.path for doc in path_documents)

    if case_info:
        set_field("序号", int(case_info["sequence"]), case_info.get("name", zip_name), 1.0, "案件分组标识")
        set_field("借款人姓名", case_info.get("name"), case_info.get("name", zip_name), 0.99, "案件分组标识")
        if case_info.get("account"):
            set_field("贷款账号", case_info["account"], case_info.get("name", zip_name), 1.0, "案件分组标识")
        if case_info.get("contract"):
            set_field("贷款合同编号", case_info["contract"], case_info.get("name", zip_name), 0.98, "案件分组标识")

    root_match = re.search(r"(?:^|[\\/])(\d{1,8})-([^-\\/]{2,30})-([0-9A-Za-z]{16,40})(?:[\\/]|$)", "\\" + zip_name)
    if not root_match:
        root_match = re.search(r"^(\d{1,8})-([^-]{2,30})-([0-9A-Za-z]{16,40})", Path(zip_name).stem)
    if root_match:
        set_field("序号", int(root_match.group(1)), Path(zip_name).name, 1.0, "压缩包命名")
        set_field("借款人姓名", root_match.group(2), Path(zip_name).name, 0.99, "压缩包命名")
        account = normalize_ocr_digits(root_match.group(3))
        if len(account) >= 16:
            set_field("贷款账号", account, Path(zip_name).name, 1.0, "压缩包命名")

    identity_candidates: list[tuple[str, SourceDocument]] = []
    for doc in preferred:
        for candidate in re.findall(r"(?<!\d)\d{17}[0-9Xx](?!\d)", normalize_text(doc.text)):
            if valid_cn_id(candidate):
                identity_candidates.append((candidate.upper(), doc))
    if identity_candidates:
        identity, source_doc = identity_candidates[0]
        set_field("借款人身份证号码", identity, source_doc.path, 1.0, source_doc.method)
        birthday, age, gender = id_details(identity)
        reference_date = detect_reference_date(all_compact)
        if reference_date:
            age = reference_date.year - int(birthday[:4])
        set_field("借款人出生日期", birthday, source_doc.path, 0.99, "身份证号推导")
        age_method = "按材料基准日年份由身份证号计算" if reference_date else "按本机日期由身份证号计算，需核对基准日"
        age_confidence = 0.9 if reference_date else 0.78
        set_field("借款人年龄", age, source_doc.path, age_confidence, age_method)
        set_field("性别", gender, source_doc.path, 0.99, "身份证号推导")

    for doc in preferred:
        ctext = compact(doc.text)
        subject_text = ctext
        subject_name = clean_value(str(case_info.get("name", ""))) if case_info else ""
        if subject_name and subject_name in ctext:
            subject_index = ctext.find(subject_name)
            subject_text = ctext[subject_index:subject_index + 900]
        name_match = re.search(r"借款人[：:]?([\u4e00-\u9fff·]{2,12})(?=身份证|住所|通讯|$)", ctext)
        if not name_match:
            name_match = re.search(
                r"被告(?:\(自然人\))?姓名[：:]?([\u4e00-\u9fff·]{2,12})(?=性别|出生|民族|工作|$)",
                ctext,
            )
        if name_match:
            set_field("借款人姓名", name_match.group(1), doc.path, 0.97, doc.method)
        phone_match = re.search(r"(?:联系电话及传真|移动电话及传真|移动电话|手机号码|联系电话)[：:]?((?:\+?86)?1[3-9]\d{9})", ctext)
        if phone_match:
            set_field("借款人手机号", phone_match.group(1)[-11:], doc.path, 0.98, doc.method)
        explicit_address = next(
            (
                match for match in re.finditer(
                    r"(?:住所地|住所所在地|户籍所在地)(?:\(户籍所在地\))?[：:]?(.{5,100}?)(?=证件类型|证件号码|联系电话|经常居住地|$)",
                    ctext,
                )
                if any(token in match.group(1) for token in ("省", "市", "区", "县"))
            ),
            None,
        )
        address_match = next(
            (
                match for match in re.finditer(
                    r"(?:借款合同约定送达地址|通讯地址|住所|住址)[：:]?(.{5,100}?)(?=通讯地址|邮政编码|联系电话|移动电话|电子邮箱|Email|其他信息|贷款人[：:]|$)",
                    ctext,
                )
                if any(token in match.group(1) for token in ("省", "市", "区", "县"))
            ),
            None,
        )
        selected_address = explicit_address or address_match
        if selected_address:
            address = selected_address.group(1).strip("，,。；;")
            if any(token in address for token in ("省", "市", "区", "县")):
                confidence = 0.97 if explicit_address else 0.96
                set_field("借款合同约定送达地址", address, doc.path, confidence, doc.method)
                set_field("借款人身份证地址", address, doc.path, 0.83, "合同住所复用，需人工核对")
                province, city = province_city(address)
                set_field("借款人身份证所在省", province, doc.path, 0.9, "地址解析")
                set_field("借款人身份证所在市", city, doc.path, 0.9, "地址解析")

    contract_candidates: list[tuple[str, SourceDocument, float]] = []
    for doc in preferred:
        ctext = compact(doc.text)
        for label, confidence in (("借款合同编号", 0.96), ("合同编号", 0.85)):
            index = ctext.find(label)
            if index >= 0:
                segment = ctext[index + len(label): index + len(label) + 70]
                raw = re.search(r"[0-9A-Za-z\[\]（）()，,。.]{16,40}", segment)
                if raw:
                    candidate = normalize_ocr_digits(raw.group(0))
                    if 16 <= len(candidate) <= 26:
                        contract_candidates.append((candidate, doc, confidence))
    if contract_candidates:
        candidate, doc, confidence = max(contract_candidates, key=lambda item: (item[2], len(item[0]) == 18))
        set_field("贷款合同编号", candidate, doc.path, confidence, doc.method)

    date_source = None
    start_date = end_date = None
    for doc in preferred:
        ctext = compact(doc.text)
        match = re.search(
            r"(?:有效期间自|期限自|起始日[为：:]?|约定期限[为：:]?)(\d{4})[年/.-](\d{1,2})[月/.-](\d{1,2})日?(?:起)?(?:至|到)(\d{4})[年/.-](\d{1,2})[月/.-](\d{1,2})日?(?:止)?",
            ctext,
        )
        if match:
            start_date = date_string(*match.group(1, 2, 3))
            end_date = date_string(*match.group(4, 5, 6))
            date_source = doc
            break
    if start_date and end_date and date_source:
        set_field("贷款发放日", start_date, date_source.path, 0.88, "合同期限/开户日期解析")
        set_field("贷款到期日", end_date, date_source.path, 0.96, date_source.method)
        months = month_difference(start_date, end_date)
        if months is not None:
            set_field("贷款合同期限（月）", months, date_source.path, 0.9, "起止日期计算")

    for doc in preferred:
        ctext = compact(doc.text)
        subject_text = ctext
        subject_name = clean_value(str(case_info.get("name", ""))) if case_info else ""
        if subject_name and subject_name in ctext:
            subject_index = ctext.find(subject_name)
            subject_text = ctext[subject_index:subject_index + 900]
        rate_match = re.search(
            r"(?:利率按照|贷款年利率|年利率|利息年化|利率(?:[☑√■●□◇◆])?)[：:]?([0-9]+(?:\.[0-9]+)?)%",
            ctext,
        )
        if rate_match:
            set_field("截至基准日执行利率", float(rate_match.group(1)), doc.path, 0.94, doc.method)
        if "固定利率" in ctext:
            set_field("贷款利率类型", "固定利率", doc.path, 0.98, doc.method)
        amount_match = re.search(r"借款额度为人民币(?:（大写）|\(大写\))?([零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟]+)元", ctext)
        if amount_match:
            amount = chinese_integer(amount_match.group(1))
            if amount:
                set_field("授信额度（元）", amount, doc.path, 0.96, doc.method)
                set_field("贷款金额", amount, doc.path, 0.88, "合同借款额度")
        numeric_amount_match = re.search(
            r"(?:借款金额|借款额度|约定金额|实际发放|借款金额约定)[：:]?(?:人民币)?([0-9][0-9,]*(?:\.[0-9]+)?)元",
            ctext,
        )
        if not numeric_amount_match:
            numeric_amount_match = re.search(
                r"借款金额(?:约定)?[：:]?([0-9][0-9,]*(?:\.[0-9]+)?)元(?:\(人民币\))?",
                ctext,
            )
        if numeric_amount_match:
            numeric_amount = parse_money(numeric_amount_match.group(1))
            set_field("授信额度（元）", numeric_amount, doc.path, 0.9, "材料数字金额")
            set_field("贷款金额", numeric_amount, doc.path, 0.94, "材料数字金额")
        if "人民币" in ctext:
            set_field("币种", "CNY-人民币", doc.path, 0.95, doc.method)
        if "一次性还本付息" in ctext or "额度到期日一次性清偿" in ctext:
            set_field("还款方式", "一次性还本付息", doc.path, 0.91, doc.method)
        purpose_match = re.search(r"借款用于(.{2,30}?)(?=。|二、|借款人特别声明|$)", ctext)
        if purpose_match:
            set_field("贷款投向行业", purpose_match.group(1), doc.path, 0.78, "合同用途，需按表口径核对")
        lender_match = re.search(r"贷款人[：:]?(.{2,45}?)(?=借款人[：:]|住所[：:]|通讯地址|邮政编码|其他信息|$)", ctext)
        if lender_match:
            short_name = lender_match.group(1).strip("，,。；;")
            if "支行" in short_name or "分行" in short_name:
                full_name = short_name
                if BANK_FULL_NAME and short_name.startswith(BANK_FULL_NAME):
                    suffix = short_name.removeprefix(BANK_FULL_NAME)
                    short_name = f"{BANK_SHORT_NAME}{suffix}" if BANK_SHORT_NAME else short_name
                elif BANK_SHORT_NAME and short_name.startswith(BANK_SHORT_NAME):
                    suffix = short_name.removeprefix(BANK_SHORT_NAME)
                    full_name = f"{BANK_FULL_NAME}{suffix}" if BANK_FULL_NAME else full_name
                set_field("经办行", short_name, doc.path, 0.97, doc.method)
                set_field("贷款发放机构简称", short_name, doc.path, 0.97, doc.method)
                set_field("贷款发放机构全称", full_name, doc.path, 0.88, "机构简称规范化，需人工核对")

        principal_match = re.search(
            r"(?:尚欠|欠付|未偿)(?:贷款)?本金(?:余额)?[：:]?([0-9][0-9,]*(?:\.[0-9]+)?)元",
            ctext,
        )
        if principal_match:
            set_field("未偿本金余额", parse_money(principal_match.group(1)), doc.path, 0.97, doc.method)
        interest_match = re.search(
            r"(?:尚欠|欠付|未偿|欠)(?:期内)?利息(?:余额)?[：:]?([0-9][0-9,]*(?:\.[0-9]+)?)元",
            ctext,
        )
        if interest_match:
            set_field("未偿利息余额", parse_money(interest_match.group(1)), doc.path, 0.96, doc.method)
        direct_fee_match = re.search(r"未偿费用(?:余额)?[：:]?([0-9][0-9,]*(?:\.[0-9]+)?)元", ctext)
        compound_match = re.search(r"复利[：:]?([0-9][0-9,]*(?:\.[0-9]+)?)元", ctext)
        penalty_match = re.search(r"罚息(?:\(违约金\))?[：:]?([0-9][0-9,]*(?:\.[0-9]+)?)元", ctext)
        if direct_fee_match:
            set_field("未偿费用", parse_money(direct_fee_match.group(1)), doc.path, 0.95, doc.method)
        elif compound_match and penalty_match:
            compound = parse_money(compound_match.group(1)) or 0
            penalty = parse_money(penalty_match.group(1)) or 0
            set_field("未偿费用", round(float(compound) + float(penalty), 2), doc.path, 0.9, "复利与罚息合计")

        overdue_match = re.search(r"基准日(?:已)?逾期[：:]?(\d{1,6})天", ctext)
        if overdue_match:
            set_field("基准日逾期天数", int(overdue_match.group(1)), doc.path, 0.96, doc.method)
        bad_days_match = re.search(r"基准日(?:进入不良|不良)(?:的天数)?[：:]?(\d{1,6})天?", ctext)
        if bad_days_match:
            set_field("基准日进入不良的天数", int(bad_days_match.group(1)), doc.path, 0.92, doc.method)
        remaining_periods_match = re.search(r"贷款剩余期数[：:]?(\d{1,5})", ctext)
        if remaining_periods_match:
            set_field("贷款剩余期数", int(remaining_periods_match.group(1)), doc.path, 0.93, doc.method)

        last_payment_match = re.search(
            r"最后一次还款[：:]?([0-9][0-9,]*(?:\.[0-9]+)?)元(?:[,，;；])?时间[：:]?"
            r"((?:20\d{2})[年/.-](?:\d{1,2})[月/.-](?:\d{1,2})日?|未提供|无)",
            ctext,
        )
        if last_payment_match:
            set_field("最后一次还款金额", parse_money(last_payment_match.group(1)), doc.path, 0.96, doc.method)
            if last_payment_match.group(2) not in {"未提供", "无"}:
                date_match = re.search(r"(20\d{2})[年/.-](\d{1,2})[月/.-](\d{1,2})", last_payment_match.group(2))
                if date_match:
                    set_field("最后一次还款时间", date_string(*date_match.groups()), doc.path, 0.95, doc.method)
        repaid_match = re.search(
            r"进入不良至基准日累计已(?:偿还|还)本金[：:]?([0-9][0-9,]*(?:\.[0-9]+)?)元",
            ctext,
        )
        if repaid_match:
            set_field("进入不良至基准日累计已偿还本金金额", parse_money(repaid_match.group(1)), doc.path, 0.94, doc.method)
        last_bad_match = re.search(
            r"最后一次不良(?:的)?(?:时间|日期)[：:]?(20\d{2})[年/.-](\d{1,2})[月/.-](\d{1,2})日?",
            ctext,
        )
        if last_bad_match:
            set_field("最后一次不良的时间", date_string(*last_bad_match.groups()), doc.path, 0.93, doc.method)
        entry_balance_patterns = (
            ("进入不良时未偿本金余额", r"进入不良时未偿本金(?:余额)?[：:]?([0-9][0-9,]*(?:\.[0-9]+)?)元?"),
            ("进入不良时未偿利息余额", r"进入不良时未偿利息(?:余额)?[：:]?([0-9][0-9,]*(?:\.[0-9]+)?)元?"),
            ("进入不良时未偿费用余额", r"进入不良时未偿费用(?:余额)?[：:]?([0-9][0-9,]*(?:\.[0-9]+)?)元?"),
        )
        for header, pattern in entry_balance_patterns:
            entry_match = re.search(pattern, ctext)
            if entry_match:
                set_field(header, parse_money(entry_match.group(1)), doc.path, 0.93, "材料明确字段")

        explicit_guarantee = re.search(r"担保方式[：:]?(信用|抵押|质押|保证)", ctext)
        if explicit_guarantee:
            set_field("担保方式", explicit_guarantee.group(1), doc.path, 0.96, "材料明确字段")
        repayment_source = re.search(r"还款来源[：:]?([\u4e00-\u9fffA-Za-z]{2,20})(?=还款方式|贷款|授信|$)", ctext)
        if repayment_source:
            set_field("还款来源", repayment_source.group(1), doc.path, 0.9, "材料明确字段")
        online_match = re.search(r"(?:线上线下|办理渠道)[：:]?(线上|线下)", ctext)
        if online_match:
            set_field("线上线下", online_match.group(1), doc.path, 0.92, "材料明确字段")

        education_match = re.search(r"(?:教育程度|最高学历)[：:]?([\u4e00-\u9fff]{2,20})(?=职业|工作|婚姻|$)", subject_text)
        if education_match:
            set_field("借款人教育程度", education_match.group(1), doc.path, 0.86, "材料明确字段")
        occupation_match = re.search(r"(?:职业|职务)[：:]?([\u4e00-\u9fff]{2,20})(?=联系|住所|工作|收入|$)", subject_text)
        if occupation_match:
            set_field("职业", occupation_match.group(1), doc.path, 0.86, "材料明确字段")
        marriage_match = re.search(r"婚姻(?:状态|状况)[：:]?([\u4e00-\u9fff]{2,20})(?=性别|职业|工作|$)", subject_text)
        if marriage_match:
            set_field("婚姻状态", marriage_match.group(1), doc.path, 0.86, "材料明确字段")

        customer_match = re.search(r"客户(?:编号|号)[：:]?(\d{16,20})(?!\d)", ctext)
        if customer_match:
            set_field("客户编号", customer_match.group(1), doc.path, 0.93, "材料明确字段")
        classification_match = re.search(r"(?:基准日)?五级分类[：:]?([\u4e00-\u9fff]{2,12})(?=是否|客户|$)", ctext)
        if classification_match:
            set_field("基准日五级分类", classification_match.group(1), doc.path, 0.9, "材料明确字段")
        employee_match = re.search(r"是否本行员工[：:]?(是|否)", ctext)
        if employee_match:
            set_field("是否本行员工", employee_match.group(1), doc.path, 0.94, "材料明确字段")
        manager_match = re.search(r"客户经理[：:]?([\u4e00-\u9fff·]{2,12})(?=贷款|机构|$)", ctext)
        if manager_match:
            set_field("客户经理", manager_match.group(1), doc.path, 0.9, "材料明确字段")
        criminal_match = re.search(r"是否涉刑[：:]?(是|否)", ctext)
        if criminal_match:
            set_field("是否涉刑", criminal_match.group(1), doc.path, 0.94, "材料明确字段")
        branch_code_match = re.search(r"贷款发放机构统一社会信用代码[：:]?([0-9A-Z]{18})", ctext)
        if branch_code_match:
            set_field("贷款发放机构统一社会信用代码", branch_code_match.group(1), doc.path, 0.96, "材料明确字段")
        head_code_match = re.search(r"(?:所属总行|总行)(?:的)?统一社会信用代码[：:]?([0-9A-Z]{18})", ctext)
        if head_code_match:
            set_field("贷款发放机构所属总行的统一社会信用代码", head_code_match.group(1), doc.path, 0.96, "材料明确字段")

    if "个人消费" in all_compact or (LOAN_PRODUCT_KEYWORD and LOAN_PRODUCT_KEYWORD in all_compact):
        source = next(
            (
                doc
                for doc in preferred
                if "个人消费" in compact(doc.text)
                or (LOAN_PRODUCT_KEYWORD and LOAN_PRODUCT_KEYWORD in compact(doc.text))
            ),
            None,
        )
        if source:
            set_field("产品类别", "个人消费类", source.path, 0.9, source.method)
    if LOAN_PRODUCT_KEYWORD and LOAN_PRODUCT_KEYWORD in all_compact:
        source = next(doc for doc in preferred if LOAN_PRODUCT_KEYWORD in compact(doc.text))
        product_name = f"{LOAN_PRODUCT_KEYWORD}（一次性还本付息）"
        product = (
            f"{LOAN_PRODUCT_CODE} - {product_name}"
            if LOAN_PRODUCT_CODE and LOAN_PRODUCT_CODE in all_compact
            else product_name
        )
        set_field("贷款种类", product, source.path, 0.93, source.method)

    if re.search(r"担保材料[^\n\\/]*[—\-_（）()]*无", all_paths):
        source = next(doc.path for doc in path_documents if "担保材料" in doc.path and "无" in doc.path)
        set_field("担保方式", "信用", source, 0.82, "无担保材料路径推断，需人工核对")
    else:
        for label in ("抵押", "质押", "保证"):
            if label in all_compact:
                source = next(doc for doc in preferred if label in compact(doc.text))
                set_field("担保方式", label, source.path, 0.8, source.method)
                break

    no_outsource = any("委外催收协议" in doc.path and "无" in doc.path for doc in path_documents)
    no_external_collection = any("催收记录" in doc.path and "行外" in doc.path and "无" in doc.path for doc in path_documents)
    if no_outsource or no_external_collection:
        source = next(doc.path for doc in path_documents if ("委外催收协议" in doc.path or "行外" in doc.path) and "无" in doc.path)
        set_field("是否委外催收", "否", source, 0.99, "材料路径明确标注无")
    elif "委外催收" in all_compact:
        source = next(doc for doc in preferred if "委外催收" in compact(doc.text))
        set_field("是否委外催收", "是", source.path, 0.8, source.method)

    no_lawsuit = any(any(keyword in doc.path for keyword in ("诉讼、仲裁、执行文件", "律师代理协议")) and "无" in doc.path for doc in path_documents)
    if no_lawsuit:
        source = next(doc.path for doc in path_documents if "诉讼、仲裁、执行文件" in doc.path and "无" in doc.path)
        set_field("是否起诉", "否", source, 0.99, "材料路径明确标注无")
    elif any(keyword in all_paths for keyword in ("起诉状", "判决书", "裁定书", "执行通知")):
        source = next(doc.path for doc in path_documents if any(keyword in doc.path for keyword in ("起诉状", "判决书", "裁定书", "执行通知")))
        set_field("是否起诉", "是", source, 0.9, "诉讼文件路径")

    address_result = fields.get("借款合同约定送达地址") or fields.get("借款人身份证地址")
    institution_result = fields.get("贷款发放机构简称")
    if institution_result:
        institution_text = str(institution_result.value)
        for bank_prefix in (BANK_FULL_NAME, BANK_SHORT_NAME):
            if bank_prefix and institution_text.startswith(bank_prefix):
                institution_text = institution_text.removeprefix(bank_prefix)
                break
        match = re.search(r"([\u4e00-\u9fff]{2,6}?市)", institution_text)
        if match:
            city = match.group(1)
            set_field("分行所在城市", city, institution_result.source, 0.86, "机构名称解析")
            if address_result:
                province, address_city = province_city(str(address_result.value))
                if address_city == city or city in str(address_result.value):
                    set_field("分行所在省份", province, institution_result.source, 0.76, "机构城市与借款人地址交叉推断")
                    set_field("贷款发放机构地区", province, institution_result.source, 0.76, "机构城市与借款人地址交叉推断")
                    if province:
                        set_field("一级分行", province + "分行", institution_result.source, 0.68, "机构地区推断，需人工核对")

    principal = fields.get("未偿本金余额")
    interest = fields.get("未偿利息余额")
    if principal and interest and isinstance(principal.value, (int, float)) and isinstance(interest.value, (int, float)):
        set_field("合计", round(principal.value + interest.value, 2), f"{principal.source}；{interest.source}", 0.98, "本金加利息")

    missing = FieldResult("无", "压缩包中未发现可靠信息", 0.0, "缺失项自动填充并标黄", True)
    return [(fields.get(header) or missing).public(header, index) for index, header in enumerate(HEADERS)]


def analyze_case(
    zip_path: Path,
    extraction_dir: Path,
    ocr_script: Path,
    progress: Callable[[str], None] | None = None,
    case_hint: dict | None = None,
) -> dict:
    if progress:
        progress("正在安全解压材料")
    files = safe_extract_zip(zip_path, extraction_dir)
    if progress:
        progress("正在读取 PDF、Word、图片与材料路径")
    all_documents, warnings = collect_documents(extraction_dir, ocr_script, progress)
    documents, selected_case, case_warnings = select_case_documents(all_documents, zip_path.name, case_hint)
    warnings.extend(case_warnings)
    fields = analyze_fields(documents, zip_path.name, selected_case)
    found = sum(1 for field in fields if not field["missing"])
    review = sum(1 for field in fields if not field["missing"] and field["confidence"] < 0.85)
    readable_paths = {
        document.path.split("::", 1)[0]
        for document in documents
        if document.kind != "path" and document.text.strip()
    }
    return {
        "fields": fields,
        "selected_case": selected_case,
        "summary": {
            "total": len(fields),
            "found": found,
            "missing": len(fields) - found,
            "review": review,
            "files": len(files),
            "files_total": len(files),
            "readable_files": len(readable_paths),
        },
        "warnings": warnings,
        "sources": [asdict(document) for document in documents if document.kind != "path"],
    }
