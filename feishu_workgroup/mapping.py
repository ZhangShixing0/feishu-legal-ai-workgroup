from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FIELD_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "templates" / "financial_loan" / "field_schema.json"
)

HEADER_TO_FIELD_ID = {
    "序号": "case_sequence",
    "客户编号": "customer_number",
    "贷款合同编号": "contract_number",
    "贷款账号": "loan_account",
    "借款人姓名": "defendant_name",
    "借款人出生日期": "defendant_birth_date",
    "借款人年龄": "defendant_age",
    "借款人教育程度": "defendant_education",
    "职业": "defendant_occupation",
    "发放贷款时借款人所在单位性质": "employment_unit_nature",
    "借款人年收入": "defendant_annual_income",
    "婚姻状态": "marital_status",
    "性别": "defendant_gender",
    "借款合同约定送达地址": "service_address",
    "借款人身份证号码": "defendant_id_number",
    "借款人身份证所在省": "defendant_id_province",
    "借款人身份证所在市": "defendant_id_city",
    "借款人身份证地址": "defendant_address",
    "借款人手机号": "defendant_phone",
    "一级分行": "primary_branch",
    "二级分行": "secondary_branch",
    "经办行": "handling_branch",
    "分行所在省份": "branch_province",
    "分行所在城市": "branch_city",
    "产品类别": "product_category",
    "贷款种类": "loan_type",
    "担保方式": "guarantee_method",
    "还款来源": "repayment_source",
    "还款方式": "repayment_method",
    "授信额度（元）": "credit_limit",
    "贷款金额": "loan_amount",
    "币种": "currency",
    "线上线下": "channel",
    "截至基准日执行利率": "effective_interest_rate",
    "贷款利率类型": "interest_rate_type",
    "贷款合同期限（月）": "contract_term_months",
    "贷款剩余期数": "remaining_periods",
    "贷款发放日": "loan_disbursement_date",
    "贷款到期日": "loan_maturity_date",
    "贷款投向行业": "loan_industry",
    "基准日逾期天数": "overdue_days",
    "基准日进入不良的天数": "npl_days_at_baseline",
    "最后一次还款时间": "last_repayment_date",
    "最后一次还款金额": "last_repayment_amount",
    "未偿本金余额": "principal",
    "未偿利息余额": "interest",
    "未偿费用": "outstanding_fees",
    "合计": "total_amount",
    "基准日五级分类": "five_level_classification",
    "是否本行员工": "is_bank_employee",
    "最后一次不良的时间": "last_npl_date",
    "进入不良时未偿本金余额": "npl_entry_principal",
    "进入不良时未偿利息余额": "npl_entry_interest",
    "进入不良时未偿费用余额": "npl_entry_fees",
    "进入不良至基准日累计已偿还本金金额": "principal_repaid_since_npl",
    "是否委外催收": "outsourced_collection",
    "委外催收机构": "collection_agency",
    "是否起诉": "is_litigated",
    "该笔贷款目前所处法律程序阶段": "legal_procedure_stage",
    "是否涉刑": "is_criminal_case",
    "客户经理": "relationship_manager",
    "贷款发放机构全称": "lending_org_full",
    "贷款发放机构简称": "lending_org_short",
    "贷款发放机构统一社会信用代码": "lending_org_uscc",
    "贷款发放机构地区": "lending_org_region",
    "贷款发放机构所属总行的统一社会信用代码": "head_office_uscc",
}

SUPPLEMENT_FIELD_IDS = (
    "contract_sign_date",
    "plaintiff_name",
    "original_creditor",
    "balance_date",
    "contract_name",
    "calculation_method",
    "compound_interest",
    "penalty_interest",
    "co_party",
    "defendant_ethnicity",
    "defendant_employer",
    "defendant_title",
    "loan_lender",
    "loan_borrower",
    "agreed_amount",
    "disbursed_amount",
)

READ_ONLY_SUMMARY_IDS = (
    "defendant_name",
    "contract_number",
    "principal",
    "interest",
    "total_amount",
    "loan_disbursement_date",
    "loan_maturity_date",
)


def load_field_metadata() -> tuple[dict[str, dict[str, Any]], set[str]]:
    schema = json.loads(FIELD_SCHEMA_PATH.read_text(encoding="utf-8"))
    metadata: dict[str, dict[str, Any]] = {}
    required: set[str] = set()
    for group in schema["groups"]:
        for field in group["fields"]:
            metadata[field["id"]] = field
            if field.get("required"):
                required.add(field["id"])
    return metadata, required


FIELD_METADATA, REQUIRED_FIELD_IDS = load_field_metadata()


def extraction_to_generator_fields(result: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for item in result.get("fields", []):
        if not isinstance(item, dict) or item.get("missing"):
            continue
        field_id = HEADER_TO_FIELD_ID.get(str(item.get("header", "")))
        value = item.get("value")
        if not field_id or value is None or str(value).strip() in {"", "无"}:
            continue
        if field_id not in fields or not str(fields[field_id]).strip():
            fields[field_id] = str(value).strip()

    copy_if_empty(fields, "loan_borrower", "defendant_name")
    copy_if_empty(fields, "defendant_title", "defendant_occupation")
    copy_if_empty(fields, "contract_sign_date", "loan_disbursement_date")
    copy_if_empty(fields, "agreed_amount", "loan_amount")
    copy_if_empty(fields, "disbursed_amount", "loan_amount")
    copy_if_empty(fields, "loan_lender", "lending_org_full")
    copy_if_empty(fields, "original_creditor", "lending_org_full")
    if not fields.get("calculation_method") and fields.get("effective_interest_rate"):
        fields["calculation_method"] = f"利息年化{fields['effective_interest_rate']}%"
    fields.setdefault("compound_interest", "0.00")
    fields.setdefault("penalty_interest", "0.00")
    return fields


def copy_if_empty(fields: dict[str, Any], target: str, source: str) -> None:
    if not str(fields.get(target, "")).strip() and str(fields.get(source, "")).strip():
        fields[target] = fields[source]


def missing_required_fields(fields: dict[str, Any]) -> list[str]:
    return sorted(
        (field_id for field_id in REQUIRED_FIELD_IDS if not str(fields.get(field_id, "")).strip()),
        key=lambda item: FIELD_METADATA[item]["label"],
    )


def label_for(field_id: str) -> str:
    return str(FIELD_METADATA.get(field_id, {}).get("label", field_id))
