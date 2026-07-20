const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { parsePastedText } = require("../static/text-parser.js");
const schema = require("../templates/financial_loan/field_schema.json");
const parse = (text) => parsePastedText(text, schema.paste_aliases);

const screenshotText = `姓名：张三          性别：男  出生日期：2000.02.05      民族： 工作单位：宝鸡市
职务：无     联系电话：19900000004    住址地（户籍所在地）：宝鸡市          证件类型：
身份证件号码：000000000000000000002`;

assert.deepEqual(parse(screenshotText), {
  defendant_name: "张三",
  defendant_gender: "男",
  defendant_birth_date: "2000-02-05",
  defendant_ethnicity: "",
  defendant_employer: "宝鸡市",
  defendant_title: "无",
  defendant_phone: "19900000004",
  defendant_address: "宝鸡市",
  defendant_id_number: "000000000000000000002",
  loan_borrower: "张三",
});

const gluedLabelText = "姓名：张三                          性别：男出生日期：2000.01.01         民族：汉 工作单位：宝鸡                          职务：无          联系电话：130945515        住所地（户籍所在地）：团结村                             证件类型：身份证证件号码：000000199004260012";

assert.deepEqual(parse(gluedLabelText), {
  defendant_name: "张三",
  defendant_gender: "男",
  defendant_birth_date: "2000-01-01",
  defendant_ethnicity: "汉",
  defendant_employer: "宝鸡",
  defendant_title: "无",
  defendant_phone: "130945515",
  defendant_address: "团结村",
  defendant_id_number: "000000199004260012",
  loan_borrower: "张三",
});

assert.deepEqual(
  parse("被告姓名 张三 性别 男 联系电话 199 0000 0004 身份证号 0000 0020 0002"),
  {
    defendant_name: "张三",
    defendant_gender: "男",
    defendant_phone: "19900000004",
    defendant_id_number: "000000200002",
    loan_borrower: "张三",
  },
);

assert.deepEqual(
  parse("合同编号： A-001  尚欠本金：人民币240,000.00元  欠款计算截止日：2025年1月31日"),
  {
    contract_number: "A-001",
    principal: "240000.00",
    balance_date: "2025-01-31",
  },
);

const fullCaseText = fs.readFileSync(path.join(__dirname, "..", "examples", "full_case_328.txt"), "utf8");
const fullCase = parse(fullCaseText);
assert.ok(Object.keys(fullCase).length >= 70, `only parsed ${Object.keys(fullCase).length} fields`);
assert.deepEqual({
  customer_number: fullCase.customer_number,
  contract_number: fullCase.contract_number,
  loan_account: fullCase.loan_account,
  principal: fullCase.principal,
  interest: fullCase.interest,
  total_amount: fullCase.total_amount,
  case_status: fullCase.case_status,
  defendant_name: fullCase.defendant_name,
  defendant_birth_date: fullCase.defendant_birth_date,
  defendant_age: fullCase.defendant_age,
  defendant_education: fullCase.defendant_education,
  employment_unit_nature: fullCase.employment_unit_nature,
  defendant_annual_income: fullCase.defendant_annual_income,
  service_address: fullCase.service_address,
  defendant_id_number: fullCase.defendant_id_number,
  defendant_address: fullCase.defendant_address,
  defendant_phone: fullCase.defendant_phone,
  loan_type: fullCase.loan_type,
  repayment_source: fullCase.repayment_source,
  repayment_method: fullCase.repayment_method,
  loan_amount: fullCase.loan_amount,
  effective_interest_rate: fullCase.effective_interest_rate,
  interest_rate_type: fullCase.interest_rate_type,
  loan_disbursement_date: fullCase.loan_disbursement_date,
  loan_maturity_date: fullCase.loan_maturity_date,
  loan_industry: fullCase.loan_industry,
  five_level_classification: fullCase.five_level_classification,
  collection_agency: fullCase.collection_agency,
  legal_procedure_stage: fullCase.legal_procedure_stage,
  lending_org_full: fullCase.lending_org_full,
  head_office_uscc: fullCase.head_office_uscc,
  loan_borrower: fullCase.loan_borrower,
  loan_lender: fullCase.loan_lender,
  original_creditor: fullCase.original_creditor,
  agreed_amount: fullCase.agreed_amount,
  disbursed_amount: fullCase.disbursed_amount,
  contract_sign_date: fullCase.contract_sign_date,
  calculation_method: fullCase.calculation_method,
}, {
  customer_number: "DEMO-CUSTOMER-001",
  contract_number: "DEMO00000000000001",
  loan_account: "00000000000000000000000001",
  principal: "266608",
  interest: "13953.03",
  total_amount: "280561.03",
  case_status: "可下户",
  defendant_name: "测试乙",
  defendant_birth_date: "1990-01-01",
  defendant_age: "35",
  defendant_education: "本科",
  employment_unit_nature: "民营企业",
  defendant_annual_income: "120000.00",
  service_address: "湖北省武汉市示例区测试路1号",
  defendant_id_number: "000000199004260012",
  defendant_address: "湖北省武汉市示例区测试路1号",
  defendant_phone: "19900000004",
  loan_type: "0217 - 建易贷（一次性还本付息）",
  repayment_source: "",
  repayment_method: "一次性还本付息",
  loan_amount: "270900.0",
  effective_interest_rate: "3.600000",
  interest_rate_type: "",
  loan_disbursement_date: "2023-07-26",
  loan_maturity_date: "2024-07-26",
  loan_industry: "",
  five_level_classification: "可疑二级",
  collection_agency: "",
  legal_procedure_stage: "",
  lending_org_full: "示例银行股份有限公司示例支行",
  head_office_uscc: "DEMO-USCC-00000002",
  loan_borrower: "测试乙",
  loan_lender: "示例银行股份有限公司示例支行",
  original_creditor: "示例银行股份有限公司示例支行",
  agreed_amount: "270900.0",
  disbursed_amount: "270900.0",
  contract_sign_date: "2023-07-26",
  calculation_method: "利息年化3.6%",
});

console.log("text parser tests passed");
