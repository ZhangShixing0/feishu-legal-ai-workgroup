from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape as html_escape
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


BASE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = BASE_DIR / "templates" / "financial_loan" / "field_schema.json"
REFERENCE_PATH = BASE_DIR / "templates" / "source" / "financial-loan-highlighted-template.docx"
REFERENCE_SHA256 = "0b4aeec11c89d9142c0c9bb012505fd5a792337d1f49ec1ded8ac6672515f036"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
XML_NS = "http://www.w3.org/XML/1998/namespace"


class GenerationError(RuntimeError):
    pass


@dataclass
class ValidationResult:
    errors: list[str]
    warnings: list[str]


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decimal_value(value: Any, field_label: str) -> Decimal:
    text = str(value or "0").replace(",", "").replace("￥", "").strip()
    try:
        return Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise GenerationError(f"{field_label}不是有效金额：{value}") from exc


def money(value: Any) -> str:
    amount = decimal_value(value, "金额")
    return f"{amount:,.2f}"


def compact_number(value: Any) -> str:
    text = str(value or "").replace("%", "").strip()
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return text
    return format(amount.normalize(), "f")


def enrich_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Fill complaint fields from equivalent Excel columns without overwriting explicit values."""
    result = dict(fields)

    def set_if_empty(target: str, source: str) -> None:
        if not str(result.get(target, "")).strip() and str(result.get(source, "")).strip():
            result[target] = result[source]

    set_if_empty("loan_borrower", "defendant_name")
    set_if_empty("defendant_title", "defendant_occupation")
    set_if_empty("defendant_address", "service_address")
    set_if_empty("contract_sign_date", "loan_disbursement_date")
    set_if_empty("agreed_amount", "loan_amount")
    set_if_empty("disbursed_amount", "loan_amount")
    set_if_empty("loan_lender", "lending_org_full")
    set_if_empty("original_creditor", "lending_org_full")
    if not str(result.get("calculation_method", "")).strip() and str(result.get("effective_interest_rate", "")).strip():
        result["calculation_method"] = f"利息年化{compact_number(result['effective_interest_rate'])}%"
    return result


def normalized_date(value: Any, label: str) -> tuple[str, str, str]:
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?", text)
    if not match:
        raise GenerationError(f"{label}请使用 YYYY-MM-DD 格式")
    return match.group(1), str(int(match.group(2))), str(int(match.group(3)))


def chinese_date(value: Any, label: str) -> str:
    year, month, day = normalized_date(value, label)
    return f"{year}年{month}月{day}日"


def validate(fields: dict[str, Any]) -> ValidationResult:
    schema = load_schema()
    errors: list[str] = []
    warnings: list[str] = []
    for group in schema["groups"]:
        for field in group["fields"]:
            if field.get("required") and not str(fields.get(field["id"], "")).strip():
                errors.append(f"“{field['label']}”为必填项")

    for field_id, label in (("contract_sign_date", "合同签订日期"), ("balance_date", "欠款计算截止日")):
        if str(fields.get(field_id, "")).strip():
            try:
                normalized_date(fields[field_id], label)
            except GenerationError as exc:
                errors.append(str(exc))

    money_fields = {
        "principal": "尚欠本金",
        "interest": "利息",
        "compound_interest": "复利",
        "penalty_interest": "罚息/违约金",
        "total_amount": "标的总额",
        "outstanding_fees": "未偿费用",
        "agreed_amount": "约定借款金额",
        "disbursed_amount": "实际发放金额",
    }
    amounts: dict[str, Decimal] = {}
    for field_id, label in money_fields.items():
        try:
            amounts[field_id] = decimal_value(fields.get(field_id), label)
        except GenerationError as exc:
            errors.append(str(exc))

    required_amounts = {"principal", "interest", "compound_interest", "penalty_interest", "total_amount"}
    if required_amounts.issubset(amounts):
        calculated = (
            amounts["principal"]
            + amounts["interest"]
            + amounts["compound_interest"]
            + amounts["penalty_interest"]
            + amounts.get("outstanding_fees", Decimal("0.00"))
        )
        if abs(calculated - amounts["total_amount"]) > Decimal("0.01"):
            warnings.append(
                f"标的总额与本金、利息、复利、罚息合计不一致：系统合计 {calculated:,.2f} 元"
            )

    lender = str(fields.get("loan_lender", ""))
    borrower = str(fields.get("loan_borrower", ""))
    if "银行" in borrower and "银行" not in lender:
        warnings.append("贷款人与借款人可能填反：借款人包含“银行”，请人工核对")
    if fields.get("defendant_name") and borrower and str(fields["defendant_name"]) not in borrower:
        warnings.append("被告姓名与借款人不一致，请确认是否属于同一案件主体")
    plaintiff = str(fields.get("plaintiff_name", "")).strip()
    if "某" in plaintiff:
        warnings.append("原告名称仍含“某”字样，可能是演示占位内容，请按实际起诉主体修改")
    defaults = schema.get("defaults", {})
    if str(fields.get("balance_date", "")).strip() == str(defaults.get("balance_date", "")).strip():
        warnings.append("欠款计算截止日仍为演示数据的基准日，请与本案台账核对")
    if str(fields.get("overdue_days", "")).strip() and amounts.get("penalty_interest") == Decimal("0.00"):
        warnings.append("案件存在逾期天数但罚息/违约金为0，请核对合同计息及罚息规则")
    return ValidationResult(errors=errors, warnings=warnings)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def paragraph_index(root: ET.Element) -> dict[str, ET.Element]:
    body = root.find(f"{W}body")
    if body is None:
        raise GenerationError("模板缺少 Word 正文")
    index: dict[str, ET.Element] = {}

    def walk(parent: ET.Element, prefix: str) -> None:
        counts: dict[str, int] = {}
        for child in list(parent):
            local = local_name(child.tag)
            position = counts.get(local, 0)
            counts[local] = position + 1
            path = f"{prefix}/{local}[{position}]"
            if local == "p":
                index[path] = child
            elif local in {"tbl", "tr", "tc", "sdt", "sdtContent", "customXml", "txbxContent"}:
                walk(child, path)

    walk(body, "word/document.xml")
    return index


def runs(paragraph: ET.Element) -> list[ET.Element]:
    return list(paragraph.iter(f"{W}r"))


def set_run_text(run: ET.Element, value: Any) -> None:
    text = str(value or "")
    for child in list(run):
        if child.tag in {f"{W}t", f"{W}tab", f"{W}br", f"{W}cr", f"{W}sym"}:
            run.remove(child)
    if not text:
        return
    node = ET.Element(f"{W}t")
    if text[:1].isspace() or text[-1:].isspace() or "  " in text:
        node.set(f"{{{XML_NS}}}space", "preserve")
    node.text = text
    rpr = run.find(f"{W}rPr")
    if rpr is not None:
        run.insert(list(run).index(rpr) + 1, node)
    else:
        run.insert(0, node)


def set_run_highlight(run: ET.Element, color: str = "yellow") -> None:
    rpr = run.find(f"{W}rPr")
    if rpr is None:
        rpr = ET.Element(f"{W}rPr")
        run.insert(0, rpr)
    highlight = rpr.find(f"{W}highlight")
    if highlight is None:
        highlight = ET.SubElement(rpr, f"{W}highlight")
    highlight.set(f"{W}val", color)


def set_at(
    paragraphs: dict[str, ET.Element],
    path: str,
    run_index: int,
    value: Any,
    *,
    preview_highlight: bool = False,
) -> None:
    paragraph = paragraphs.get(path)
    if paragraph is None:
        raise GenerationError(f"模板字段定位失败：{path}")
    paragraph_runs = runs(paragraph)
    if run_index >= len(paragraph_runs):
        raise GenerationError(f"模板字段运行位置失效：{path} run {run_index}")
    target_run = paragraph_runs[run_index]
    set_run_text(target_run, value)
    if preview_highlight and str(value or ""):
        set_run_highlight(target_run)


def set_yellow_line(
    paragraphs: dict[str, ET.Element],
    path: str,
    value: str,
    *,
    preview_highlight: bool = False,
) -> None:
    paragraph = paragraphs.get(path)
    if paragraph is None:
        raise GenerationError(f"模板字段定位失败：{path}")
    highlighted_runs: list[ET.Element] = []
    for run in runs(paragraph):
        rpr = run.find(f"{W}rPr")
        highlight = rpr.find(f"{W}highlight") if rpr is not None else None
        if highlight is not None and highlight.get(f"{W}val") == "yellow":
            highlighted_runs.append(run)
    if not highlighted_runs:
        raise GenerationError(f"模板黄色字段定位失败：{path}")
    set_run_text(highlighted_runs[0], value)
    if preview_highlight and value:
        set_run_highlight(highlighted_runs[0])
    for run in highlighted_runs[1:]:
        set_run_text(run, "")


def set_paragraph_text(
    paragraphs: dict[str, ET.Element],
    path: str,
    value: str,
    *,
    preview_highlight: bool = False,
) -> None:
    paragraph = paragraphs.get(path)
    if paragraph is None:
        raise GenerationError(f"模板字段定位失败：{path}")
    paragraph_runs = runs(paragraph)
    if not paragraph_runs:
        raise GenerationError(f"模板字段运行位置失效：{path}")
    set_run_text(paragraph_runs[0], value)
    if preview_highlight and value:
        set_run_highlight(paragraph_runs[0])
    for run in paragraph_runs[1:]:
        set_run_text(run, "")


def remove_yellow_highlights(root: ET.Element) -> None:
    """Remove only yellow review marks from the downloadable Word copy."""
    for rpr in root.iter(f"{W}rPr"):
        for highlight in list(rpr.findall(f"{W}highlight")):
            value = (highlight.get(f"{W}val") or "").lower()
            if value in {"yellow", "ffff00"}:
                rpr.remove(highlight)


def namespace_declarations(xml_bytes: bytes) -> list[tuple[str, str]]:
    head = xml_bytes[:8000].decode("utf-8", errors="ignore")
    return re.findall(r'xmlns:([A-Za-z0-9_]+)="([^"]+)"', head)


def register_namespaces(xml_bytes: bytes) -> list[tuple[str, str]]:
    declarations = namespace_declarations(xml_bytes)
    for prefix, uri in declarations:
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:
            pass
    ET.register_namespace("w", W_NS)
    return declarations


def restore_namespace_declarations(
    xml_bytes: bytes,
    declarations: list[tuple[str, str]],
) -> bytes:
    """Keep declarations referenced only by mc:Ignorable.

    ElementTree drops unused namespace declarations such as w15/wp14. Word still
    requires those prefixes to be declared because their names appear inside the
    mc:Ignorable attribute value, even when no element currently uses them.
    """
    xml = xml_bytes.decode("utf-8")
    missing = [
        f'xmlns:{prefix}="{uri}"'
        for prefix, uri in declarations
        if f'xmlns:{prefix}=' not in xml[:8000]
    ]
    if missing:
        marker = "<w:document "
        xml = xml.replace(marker, marker + " ".join(missing) + " ", 1)
    return xml.encode("utf-8")


def patch_document_xml(
    xml_bytes: bytes,
    fields: dict[str, Any],
    *,
    preview_highlights: bool = False,
    strip_yellow: bool = True,
) -> bytes:
    declarations = register_namespaces(xml_bytes)
    root = ET.fromstring(xml_bytes)
    paragraphs = paragraph_index(root)

    party_base = "word/document.xml/tbl[0]/tr[5]/tc[1]"
    set_at(
        paragraphs,
        f"{party_base}/p[0]",
        1,
        fields["defendant_name"],
        preview_highlight=preview_highlights,
    )
    set_at(paragraphs, f"{party_base}/p[0]", 2, "      ")
    set_at(
        paragraphs,
        f"{party_base}/p[0]",
        4,
        f"{fields.get('defendant_gender') or '不详'}☑",
        preview_highlight=preview_highlights,
    )
    set_at(paragraphs, f"{party_base}/p[0]", 5, "")
    set_at(paragraphs, f"{party_base}/p[0]", 6, "")
    set_at(
        paragraphs,
        f"{party_base}/p[1]",
        1,
        fields.get("defendant_birth_date", ""),
        preview_highlight=preview_highlights,
    )
    set_at(paragraphs, f"{party_base}/p[1]", 2, "  ")
    set_at(
        paragraphs,
        f"{party_base}/p[1]",
        5,
        fields.get("defendant_ethnicity", ""),
        preview_highlight=preview_highlights,
    )
    set_at(
        paragraphs,
        f"{party_base}/p[2]",
        1,
        fields.get("defendant_employer", ""),
        preview_highlight=preview_highlights,
    )
    set_at(
        paragraphs,
        f"{party_base}/p[2]",
        3,
        fields.get("defendant_title", ""),
        preview_highlight=preview_highlights,
    )
    set_at(paragraphs, f"{party_base}/p[2]", 4, "")
    set_at(
        paragraphs,
        f"{party_base}/p[2]",
        7,
        fields.get("defendant_phone", ""),
        preview_highlight=preview_highlights,
    )
    set_at(
        paragraphs,
        f"{party_base}/p[3]",
        1,
        fields.get("defendant_address", ""),
        preview_highlight=preview_highlights,
    )
    set_at(paragraphs, f"{party_base}/p[5]", 2, " ")
    set_at(
        paragraphs,
        f"{party_base}/p[5]",
        3,
        fields.get("defendant_id_number", ""),
        preview_highlight=preview_highlights,
    )

    narrative = "word/document.xml/tbl[0]/tr[10]/tc[0]/p[0]"
    year, month, day = normalized_date(fields["contract_sign_date"], "合同签订日期")
    set_at(paragraphs, narrative, 1, fields["defendant_name"], preview_highlight=preview_highlights)
    set_at(paragraphs, narrative, 3, fields.get("co_party") or "无", preview_highlight=preview_highlights)
    set_at(paragraphs, narrative, 5, year, preview_highlight=preview_highlights)
    set_at(paragraphs, narrative, 7, month, preview_highlight=preview_highlights)
    set_at(paragraphs, narrative, 9, day, preview_highlight=preview_highlights)
    set_at(paragraphs, narrative, 11, fields["contract_number"], preview_highlight=preview_highlights)
    set_at(paragraphs, narrative, 13, fields["plaintiff_name"], preview_highlight=preview_highlights)
    set_at(paragraphs, narrative, 15, fields["original_creditor"], preview_highlight=preview_highlights)

    principal_line = (
        f"截至{chinese_date(fields['balance_date'], '欠款计算截止日')}止，尚欠本金 "
        f"{money(fields['principal'])}元（人民币，下同；如外币需特别注明）；"
    )
    interest_line = (
        f"截至{chinese_date(fields['balance_date'], '欠款计算截止日')}止，欠利息"
        f"{money(fields['interest'])}元、复利{money(fields.get('compound_interest'))}元、"
        f"罚息（违约金）{money(fields['penalty_interest'])}元；计算方式：{fields['calculation_method']}"
    )
    set_yellow_line(
        paragraphs,
        "word/document.xml/tbl[0]/tr[11]/tc[1]/p[0]",
        principal_line,
        preview_highlight=preview_highlights,
    )
    set_yellow_line(
        paragraphs,
        "word/document.xml/tbl[0]/tr[12]/tc[1]/p[0]",
        interest_line,
        preview_highlight=preview_highlights,
    )
    set_at(
        paragraphs,
        "word/document.xml/tbl[0]/tr[18]/tc[1]/p[0]",
        0,
        money(fields["total_amount"]),
        preview_highlight=preview_highlights,
    )

    set_at(
        paragraphs,
        "word/document.xml/tbl[0]/tr[24]/tc[1]/p[0]",
        0,
        f"《{fields['contract_name']}》，{fields['contract_number']}",
        preview_highlight=preview_highlights,
    )
    set_at(
        paragraphs,
        "word/document.xml/tbl[0]/tr[25]/tc[1]/p[0]",
        1,
        fields["loan_lender"],
        preview_highlight=preview_highlights,
    )
    set_at(
        paragraphs,
        "word/document.xml/tbl[0]/tr[25]/tc[1]/p[1]",
        1,
        fields["loan_borrower"],
        preview_highlight=preview_highlights,
    )
    set_at(
        paragraphs,
        "word/document.xml/tbl[0]/tr[26]/tc[1]/p[0]",
        1,
        f"{money(fields['agreed_amount'])}元（人民币）",
        preview_highlight=preview_highlights,
    )
    set_at(
        paragraphs,
        "word/document.xml/tbl[0]/tr[26]/tc[1]/p[1]",
        1,
        f"{money(fields['disbursed_amount'])}元（人民币）",
        preview_highlight=preview_highlights,
    )

    if fields.get("loan_disbursement_date") and fields.get("loan_maturity_date"):
        start_date = chinese_date(fields["loan_disbursement_date"], "贷款发放日")
        maturity_date = chinese_date(fields["loan_maturity_date"], "贷款到期日")
        due = True
        if fields.get("balance_date"):
            maturity_key = tuple(map(int, normalized_date(fields["loan_maturity_date"], "贷款到期日")))
            balance_key = tuple(map(int, normalized_date(fields["balance_date"], "欠款计算截止日")))
            due = maturity_key <= balance_key
        set_paragraph_text(
            paragraphs,
            "word/document.xml/tbl[0]/tr[27]/tc[1]/p[0]",
            f"是否到期：是{'☑' if due else '□'}  否{'□' if due else '☑'}",
            preview_highlight=preview_highlights,
        )
        set_paragraph_text(
            paragraphs,
            "word/document.xml/tbl[0]/tr[27]/tc[1]/p[1]",
            f"约定期限：{start_date}起至{maturity_date}止",
            preview_highlight=preview_highlights,
        )

    if fields.get("effective_interest_rate"):
        set_paragraph_text(
            paragraphs,
            "word/document.xml/tbl[0]/tr[28]/tc[1]/p[0]",
            f"利率☑{compact_number(fields['effective_interest_rate'])}%/年（合同条款：第三条）",
            preview_highlight=preview_highlights,
        )

    if fields.get("loan_disbursement_date") and fields.get("loan_amount"):
        set_paragraph_text(
            paragraphs,
            "word/document.xml/tbl[0]/tr[29]/tc[1]/p[0]",
            f"{chinese_date(fields['loan_disbursement_date'], '贷款发放日')}，{money(fields['loan_amount'])}元。",
            preview_highlight=preview_highlights,
        )

    repayment_method = str(fields.get("repayment_method", "")).strip()
    if repayment_method:
        repayment_options = [
            ("等额本息", "等额本息"),
            ("等额本金", "等额本金"),
            ("到期一次性还本付息", "一次性还本付息"),
            ("按月计息、到期一次性还本", "按月计息、到期一次性还本"),
            ("按季计息、到期一次性还本", "按季计息、到期一次性还本"),
            ("按年计息、到期一次性还本", "按年计息、到期一次性还本"),
        ]
        matched = False
        for index, (label, keyword) in enumerate(repayment_options):
            selected = keyword in repayment_method
            matched = matched or selected
            set_paragraph_text(
                paragraphs,
                f"word/document.xml/tbl[0]/tr[30]/tc[1]/p[{index}]",
                f"{label}{'☑' if selected else '□'}",
                preview_highlight=preview_highlights and selected,
            )
        set_paragraph_text(
            paragraphs,
            "word/document.xml/tbl[0]/tr[30]/tc[1]/p[6]",
            f"其他{'□' if matched else '☑'}{'' if matched else '：' + repayment_method}",
            preview_highlight=preview_highlights and not matched,
        )

    if fields.get("principal_repaid_since_npl") or fields.get("last_repayment_amount"):
        set_paragraph_text(
            paragraphs,
            "word/document.xml/tbl[0]/tr[31]/tc[1]/p[0]",
            f"进入不良至基准日累计已还本金：{money(fields.get('principal_repaid_since_npl'))}元",
            preview_highlight=preview_highlights,
        )
        last_date = str(fields.get("last_repayment_date", "")).strip()
        date_text = chinese_date(last_date, "最后一次还款时间") if last_date else "未提供"
        set_paragraph_text(
            paragraphs,
            "word/document.xml/tbl[0]/tr[31]/tc[1]/p[1]",
            f"最后一次还款：{money(fields.get('last_repayment_amount'))}元，时间：{date_text}",
            preview_highlight=preview_highlights,
        )

    if str(fields.get("overdue_days", "")).strip():
        overdue = decimal_value(fields.get("overdue_days"), "基准日逾期天数") > 0
        set_paragraph_text(
            paragraphs,
            "word/document.xml/tbl[0]/tr[32]/tc[1]/p[0]",
            f"是{'☑' if overdue else '□'}  基准日已逾期{fields.get('overdue_days')}天",
            preview_highlight=preview_highlights and overdue,
        )
        set_paragraph_text(
            paragraphs,
            "word/document.xml/tbl[0]/tr[32]/tc[1]/p[1]",
            f"否{'□' if overdue else '☑'}",
            preview_highlight=preview_highlights and not overdue,
        )

    guarantee_method = str(fields.get("guarantee_method", "")).strip()
    if guarantee_method:
        has_property_security = any(keyword in guarantee_method for keyword in ("抵押", "质押"))
        set_paragraph_text(
            paragraphs,
            "word/document.xml/tbl[0]/tr[33]/tc[1]/p[0]",
            f"是{'☑' if has_property_security else '□'}  担保方式：{guarantee_method}",
            preview_highlight=preview_highlights,
        )
        set_paragraph_text(
            paragraphs,
            "word/document.xml/tbl[0]/tr[33]/tc[1]/p[1]",
            f"否{'□' if has_property_security else '☑'}",
            preview_highlight=preview_highlights,
        )
    if strip_yellow:
        remove_yellow_highlights(root)
    serialized = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return restore_namespace_declarations(serialized, declarations)


def _property_value(parent: ET.Element | None, name: str) -> str | None:
    if parent is None:
        return None
    node = parent.find(f"{W}{name}")
    if node is None:
        return None
    return node.get(f"{W}val")


def _enabled_property(parent: ET.Element | None, name: str) -> bool:
    value = _property_value(parent, name)
    return value is not None and value.lower() not in {"0", "false", "off", "none"}


def _wingdings_to_unicode(text: str, run: ET.Element) -> str:
    rpr = run.find(f"{W}rPr")
    fonts = rpr.find(f"{W}rFonts") if rpr is not None else None
    font_values = [] if fonts is None else [str(value) for value in fonts.attrib.values()]
    if not any("wingdings" in value.lower() for value in font_values):
        return text
    return text.translate(str.maketrans({"£": "□", "\uf0a3": "□", "R": "☑", "þ": "☑"}))


def _run_html(run: ET.Element) -> str:
    chunks: list[str] = []
    for node in list(run):
        local = local_name(node.tag)
        if local == "rPr":
            continue
        if local in {"t", "delText", "instrText"} and node.text:
            chunks.append(html_escape(_wingdings_to_unicode(node.text, run)))
        elif local == "tab":
            chunks.append('<span class="word-tab">　　</span>')
        elif local in {"br", "cr"}:
            chunks.append("<br>")
        elif local == "noBreakHyphen":
            chunks.append("&#8209;")
        elif local == "sym":
            char_value = node.get(f"{W}char", "")
            chunks.append("□" if char_value.upper().endswith("A3") else "")
    content = "".join(chunks)
    if not content:
        return ""

    rpr = run.find(f"{W}rPr")
    classes = ["word-run"]
    styles: list[str] = []
    highlight = _property_value(rpr, "highlight")
    if highlight and highlight.lower() in {"yellow", "ffff00"}:
        classes.append("word-fill-highlight")
    elif highlight and highlight.lower() == "red":
        classes.append("word-template-highlight")
    if _enabled_property(rpr, "b") or _enabled_property(rpr, "bCs"):
        styles.append("font-weight:700")
    if _enabled_property(rpr, "i") or _enabled_property(rpr, "iCs"):
        styles.append("font-style:italic")
    underline = _property_value(rpr, "u")
    if underline and underline.lower() not in {"none", "0", "false"}:
        styles.append("text-decoration:underline")
    size = _property_value(rpr, "sz") or _property_value(rpr, "szCs")
    if size and size.isdigit():
        styles.append(f"font-size:{int(size) / 2:g}pt")
    color = _property_value(rpr, "color")
    if color and re.fullmatch(r"[0-9A-Fa-f]{6}", color):
        styles.append(f"color:#{color}")
    style_attr = f' style="{";".join(styles)}"' if styles else ""
    fill_attr = ' data-preview-fill="true"' if "word-fill-highlight" in classes else ""
    return f'<span class="{" ".join(classes)}"{style_attr}{fill_attr}>{content}</span>'


def _paragraph_html(paragraph: ET.Element) -> str:
    ppr = paragraph.find(f"{W}pPr")
    styles: list[str] = []
    alignment = _property_value(ppr, "jc")
    alignment_map = {"both": "justify", "distribute": "justify"}
    if alignment:
        styles.append(f"text-align:{alignment_map.get(alignment, alignment)}")
    indent = ppr.find(f"{W}ind") if ppr is not None else None
    if indent is not None:
        left = indent.get(f"{W}left") or indent.get(f"{W}start")
        first = indent.get(f"{W}firstLine")
        hanging = indent.get(f"{W}hanging")
        if left and left.lstrip("-").isdigit():
            styles.append(f"margin-left:{int(left) / 20:g}pt")
        if first and first.lstrip("-").isdigit():
            styles.append(f"text-indent:{int(first) / 20:g}pt")
        elif hanging and hanging.lstrip("-").isdigit():
            styles.append(f"text-indent:-{int(hanging) / 20:g}pt")
    spacing = ppr.find(f"{W}spacing") if ppr is not None else None
    if spacing is not None:
        before = spacing.get(f"{W}before")
        after = spacing.get(f"{W}after")
        if before and before.isdigit():
            styles.append(f"margin-top:{int(before) / 20:g}pt")
        if after and after.isdigit():
            styles.append(f"margin-bottom:{int(after) / 20:g}pt")
    style_attr = f' style="{";".join(styles)}"' if styles else ""
    content = "".join(_run_html(run) for run in runs(paragraph)) or "&nbsp;"
    return f'<p class="word-paragraph"{style_attr}>{content}</p>'


def _cell_span(cell: ET.Element) -> int:
    tcpr = cell.find(f"{W}tcPr")
    value = _property_value(tcpr, "gridSpan")
    return max(1, int(value)) if value and value.isdigit() else 1


def _vertical_merge(cell: ET.Element) -> str | None:
    tcpr = cell.find(f"{W}tcPr")
    if tcpr is None:
        return None
    node = tcpr.find(f"{W}vMerge")
    if node is None:
        return None
    return "restart" if node.get(f"{W}val") == "restart" else "continue"


def _cell_width_style(cell: ET.Element) -> str:
    tcpr = cell.find(f"{W}tcPr")
    width = tcpr.find(f"{W}tcW") if tcpr is not None else None
    value = width.get(f"{W}w") if width is not None else None
    if value and value.isdigit():
        return f"width:{int(value) / 20:g}pt"
    return ""


def _cell_html(cell: ET.Element) -> str:
    content: list[str] = []
    for child in list(cell):
        local = local_name(child.tag)
        if local == "p":
            content.append(_paragraph_html(child))
        elif local == "tbl":
            content.append(_table_html(child))
    return "".join(content) or '<p class="word-paragraph">&nbsp;</p>'


def _table_html(table: ET.Element) -> str:
    rows_xml = table.findall(f"{W}tr")
    row_cells: list[list[dict[str, Any]]] = []
    for row in rows_xml:
        column = 0
        cells: list[dict[str, Any]] = []
        for cell in row.findall(f"{W}tc"):
            span = _cell_span(cell)
            cells.append({
                "element": cell,
                "column": column,
                "span": span,
                "merge": _vertical_merge(cell),
            })
            column += span
        row_cells.append(cells)

    output_rows: list[str] = []
    for row_index, cells in enumerate(row_cells):
        rendered_cells: list[str] = []
        for cell_info in cells:
            if cell_info["merge"] == "continue":
                continue
            attributes: list[str] = []
            if cell_info["span"] > 1:
                attributes.append(f'colspan="{cell_info["span"]}"')
            if cell_info["merge"] == "restart":
                rowspan = 1
                for following_cells in row_cells[row_index + 1:]:
                    match = next(
                        (item for item in following_cells if item["column"] == cell_info["column"]),
                        None,
                    )
                    if match is None or match["merge"] != "continue":
                        break
                    rowspan += 1
                if rowspan > 1:
                    attributes.append(f'rowspan="{rowspan}"')
            width_style = _cell_width_style(cell_info["element"])
            if width_style:
                attributes.append(f'style="{width_style}"')
            attr_text = (" " + " ".join(attributes)) if attributes else ""
            rendered_cells.append(
                f'<td class="word-cell"{attr_text}>{_cell_html(cell_info["element"])}</td>'
            )
        output_rows.append(
            f'<tr class="word-row" data-word-row="{row_index}">{"".join(rendered_cells)}</tr>'
        )
    return f'<table class="word-document-table"><tbody>{"".join(output_rows)}</tbody></table>'


def document_xml_to_preview_html(xml_bytes: bytes) -> str:
    root = ET.fromstring(xml_bytes)
    body = root.find(f"{W}body")
    if body is None:
        raise GenerationError("生成的Word缺少正文，无法建立预览")
    content: list[str] = []
    for child in list(body):
        local = local_name(child.tag)
        if local == "p":
            content.append(_paragraph_html(child))
        elif local == "tbl":
            content.append(_table_html(child))
    return f'<article class="word-preview-paper">{"".join(content)}</article>'


def generate_preview_html(fields: dict[str, Any]) -> str:
    """Build a fast, full-document preview from the same Word template mapping."""
    fields = enrich_fields(fields)
    with zipfile.ZipFile(REFERENCE_PATH, "r") as source:
        source_xml = source.read("word/document.xml")
    preview_xml = patch_document_xml(
        source_xml,
        fields,
        preview_highlights=True,
        strip_yellow=False,
    )
    return document_xml_to_preview_html(preview_xml)


def generate_document(fields: dict[str, Any], output_path: Path) -> ValidationResult:
    if not REFERENCE_PATH.exists():
        raise GenerationError(f"找不到模板：{REFERENCE_PATH}")
    if sha256(REFERENCE_PATH) != REFERENCE_SHA256:
        raise GenerationError("模板文件与已验证版本不一致，请重新建立模板基线")

    fields = enrich_fields(fields)
    result = validate(fields)
    if result.errors:
        raise GenerationError("；".join(result.errors))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(REFERENCE_PATH, "r") as source, zipfile.ZipFile(output_path, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "word/document.xml":
                data = patch_document_xml(data, fields)
            target.writestr(info, data)
    return result


def package_fidelity(reference: Path, generated: Path) -> dict[str, Any]:
    with zipfile.ZipFile(reference) as left, zipfile.ZipFile(generated) as right:
        left_names = left.namelist()
        right_names = right.namelist()
        changed = [
            name
            for name in left_names
            if name in right_names and left.read(name) != right.read(name)
        ]
        return {
            "same_part_set": left_names == right_names,
            "changed_parts": changed,
            "preserve_only_ok": set(changed).issubset({"word/document.xml"}),
        }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("fields_json", type=Path)
    parser.add_argument("output_docx", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.fields_json.read_text(encoding="utf-8"))
    validation = generate_document(payload, args.output_docx)
    print(json.dumps({
        "output": str(args.output_docx),
        "warnings": validation.warnings,
        "fidelity": package_fidelity(REFERENCE_PATH, args.output_docx),
    }, ensure_ascii=False, indent=2))
