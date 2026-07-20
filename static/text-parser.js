(function exposeTextParser(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.LegalTextParser = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function createTextParser() {
  const baseLabelAliases = {
    "被告姓名": "defendant_name",
    "被告名称": "defendant_name",
    "姓名": "defendant_name",
    "性别": "defendant_gender",
    "出生年月日": "defendant_birth_date",
    "出生年月": "defendant_birth_date",
    "出生日期": "defendant_birth_date",
    "民族": "defendant_ethnicity",
    "工作单位": "defendant_employer",
    "任职单位": "defendant_employer",
    "职务": "defendant_title",
    "联系电话": "defendant_phone",
    "联系方式": "defendant_phone",
    "手机号码": "defendant_phone",
    "手机号": "defendant_phone",
    "电话": "defendant_phone",
    "住址地（户籍所在地）": "defendant_address",
    "住址地(户籍所在地)": "defendant_address",
    "住所地（户籍所在地）": "defendant_address",
    "住所地(户籍所在地)": "defendant_address",
    "住所地": "defendant_address",
    "住址地": "defendant_address",
    "居住地址": "defendant_address",
    "户籍所在地": "defendant_address",
    "户籍地址": "defendant_address",
    "户籍地": "defendant_address",
    "身份证件号码": "defendant_id_number",
    "公民身份号码": "defendant_id_number",
    "身份证号码": "defendant_id_number",
    "身份证号": "defendant_id_number",
    "证件号码": "defendant_id_number",
    "证件号": "defendant_id_number",
    "共同债务人": "co_party",
    "共同签约人": "co_party",
    "合同签订日期": "contract_sign_date",
    "合同签订时间": "contract_sign_date",
    "签订日期": "contract_sign_date",
    "签订时间": "contract_sign_date",
    "合同编号": "contract_number",
    "原告名称": "plaintiff_name",
    "原告": "plaintiff_name",
    "债权转让前债权人": "original_creditor",
    "原债权人": "original_creditor",
    "欠款计算截止日": "balance_date",
    "欠款计算截止日期": "balance_date",
    "计算截止日期": "balance_date",
    "截止日期": "balance_date",
    "尚欠本金": "principal",
    "欠款本金": "principal",
    "本金": "principal",
    "利息": "interest",
    "复利": "compound_interest",
    "罚息": "penalty_interest",
    "违约金": "penalty_interest",
    "计算方式": "calculation_method",
    "标的总额": "total_amount",
    "合同名称": "contract_name",
    "贷款人": "loan_lender",
    "贷款方": "loan_lender",
    "借款人": "loan_borrower",
    "借款方": "loan_borrower",
    "约定借款金额": "agreed_amount",
    "借款金额": "agreed_amount",
    "合同金额": "agreed_amount",
    "约定金额": "agreed_amount",
    "实际发放金额": "disbursed_amount",
    "实际放款金额": "disbursed_amount",
    "放款金额": "disbursed_amount",
    "实际放款": "disbursed_amount",

    // These labels have no field in the current template, but they must end the
    // preceding value so that text such as “地址：… 证件类型： 身份证号：…”
    // is segmented correctly.
    "证件类型": null,
    "证件种类": null,
    "邮政编码": null,
    "邮编": null,
    "备注": null,
  };

  const dateFields = new Set([
    "defendant_birth_date", "contract_sign_date", "balance_date",
    "loan_disbursement_date", "loan_maturity_date", "last_repayment_date", "last_npl_date",
  ]);
  const moneyFields = new Set([
    "principal", "interest", "compound_interest", "penalty_interest",
    "total_amount", "agreed_amount", "disbursed_amount", "defendant_annual_income",
    "credit_limit", "loan_amount", "last_repayment_amount", "outstanding_fees",
    "npl_entry_principal", "npl_entry_interest", "npl_entry_fees", "principal_repaid_since_npl",
  ]);
  const compactFields = new Set([
    "defendant_phone", "defendant_id_number", "contract_number", "customer_number",
    "loan_account", "lending_org_uscc", "head_office_uscc",
  ]);

  function escapeRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function normalizeSource(text) {
    return String(text || "")
      .replace(/[\u00a0\u3000]/g, " ")
      .replace(/[\t\r\n]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function trimValue(value) {
    return value
      .replace(/^[\s,，;；。|'"“”‘’]+/, "")
      .replace(/[\s,，;；。|'"“”‘’]+$/, "")
      .trim();
  }

  function normalizeDate(value) {
    const match = value.match(/^(\d{4})\s*(?:年|[.\/-])\s*(\d{1,2})\s*(?:月|[.\/-])\s*(\d{1,2})\s*(?:日)?$/);
    if (!match) return value;
    return `${match[1]}-${match[2].padStart(2, "0")}-${match[3].padStart(2, "0")}`;
  }

  function normalizeFieldValue(fieldId, rawValue) {
    let value = trimValue(rawValue);
    if (!value) return "";
    if (dateFields.has(fieldId)) return normalizeDate(value);
    if (fieldId === "defendant_gender") {
      if (/^男(?:性)?$/.test(value)) return "男";
      if (/^女(?:性)?$/.test(value)) return "女";
    }
    if (moneyFields.has(fieldId)) {
      value = value.replace(/^(?:人民币|RMB)\s*/i, "").replace(/\s*(?:元|人民币)$/, "");
      return value.replace(/[,，\s]/g, "");
    }
    if (compactFields.has(fieldId)) return value.replace(/\s+/g, "");
    return value;
  }

  function setIfEmpty(values, target, source) {
    if (!String(values[target] || "").trim() && String(values[source] || "").trim()) {
      values[target] = values[source];
    }
  }

  function compactRate(value) {
    const text = String(value || "").replace(/%/g, "").trim();
    const amount = Number(text);
    return Number.isFinite(amount) ? String(amount) : text;
  }

  function deriveRelatedFields(values) {
    setIfEmpty(values, "loan_borrower", "defendant_name");
    setIfEmpty(values, "defendant_title", "defendant_occupation");
    setIfEmpty(values, "defendant_address", "service_address");
    setIfEmpty(values, "contract_sign_date", "loan_disbursement_date");
    setIfEmpty(values, "agreed_amount", "loan_amount");
    setIfEmpty(values, "disbursed_amount", "loan_amount");
    setIfEmpty(values, "loan_lender", "lending_org_full");
    setIfEmpty(values, "original_creditor", "lending_org_full");
    if (!String(values.calculation_method || "").trim() && String(values.effective_interest_rate || "").trim()) {
      values.calculation_method = `利息年化${compactRate(values.effective_interest_rate)}%`;
    }
    return values;
  }

  function parsePastedText(text, customAliases = {}) {
    const labelAliases = { ...baseLabelAliases, ...customAliases };
    const labels = Object.keys(labelAliases).sort((left, right) => right.length - left.length);
    const labelPattern = labels.map(escapeRegExp).join("|");
    // Complete Excel exports are often pasted without colons, whitespace or line
    // breaks. Longest-label-first scanning lets the next known label terminate
    // the previous value, including empty values between two adjacent labels.
    const labelMatcher = new RegExp(`(${labelPattern})\\s*(?:[:：]\\s*)?`, "gu");
    const source = normalizeSource(text);
    const matches = [];
    labelMatcher.lastIndex = 0;
    let match;
    while ((match = labelMatcher.exec(source)) !== null) {
      matches.push({ label: match[1], start: match.index, valueStart: labelMatcher.lastIndex });
    }

    const values = {};
    matches.forEach((item, index) => {
      const fieldId = labelAliases[item.label];
      if (!fieldId) return;
      const valueEnd = index + 1 < matches.length ? matches[index + 1].start : source.length;
      values[fieldId] = normalizeFieldValue(fieldId, source.slice(item.valueStart, valueEnd));
    });
    return deriveRelatedFields(values);
  }

  return { parsePastedText, normalizeFieldValue, deriveRelatedFields };
}));
