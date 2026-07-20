# 金融借款合同纠纷民事起诉状模板契约

## Reference

- Retained reference: `templates/source/financial-loan-highlighted-template.docx`（与原中文文件字节一致，ASCII文件名用于兼容Linux解压）
- SHA-256: `cdac10c76fef439949204c29dd22811243f95cf56d13ce65d5c81027c2b15052`
- Page count: 6（Microsoft Word PDF 导出基线）
- Section count: 1
- Evidence: `work/template-distill/template-evidence.json`
- Style evidence: `work/template-distill/style-evidence.json`
- Baseline render: `work/template-distill/reference-render/`

The retained reference is immutable. Runtime generation always writes a new DOCX.

## Page system

- A4 portrait: 8.27 × 11.69 in.
- Margins: left/right 1.25 in; top/bottom 1.00 in.
- One section, no special first-page header, no odd/even header split.
- Header/footer remain linked to the only section and are preserve-only.

## Typography and structure

- Primary Chinese typeface: 宋体.
- Checkbox glyphs: Wingdings 2.
- Title and section labels use direct formatting rather than Word heading styles.
- One large table carries the form; the source contains 212 paragraphs and one table.
- The source uses extensive direct run and paragraph formatting. Do not normalize styles.
- Page patterns: title/instructions and party information; party continuation; claims; facts; remaining facts and mediation; mediation/signature.

## Editable slot map

Only `word/document.xml` is editable. All other package parts and relationships are preserve-only.

| Semantic slot | Stable locator | Capacity / behavior |
| --- | --- | --- |
| Defendant name and gender | `tbl[0]/tr[5]/tc[1]/p[0]` | Short inline values; reconstruct only mapped runs |
| Birth date and ethnicity | `tbl[0]/tr[5]/tc[1]/p[1]` | Short inline values |
| Employer, title, phone | `tbl[0]/tr[5]/tc[1]/p[2]` | Employer ≤ 30 Chinese chars; other values short |
| Address | `tbl[0]/tr[5]/tc[1]/p[3]` | May wrap within the existing cell |
| ID number | `tbl[0]/tr[5]/tc[1]/p[5]` | Short inline value |
| Claim narrative variables | `tbl[0]/tr[10]/tc[0]/p[0]` | Six mapped variables inside preserved fixed prose |
| Principal claim line | `tbl[0]/tr[11]/tc[1]/p[0]` | Rebuild highlighted line only |
| Interest claim line | `tbl[0]/tr[12]/tc[1]/p[0]` | Rebuild highlighted line only |
| Total amount | `tbl[0]/tr[18]/tc[1]/p[0]` | Numeric value only; trailing 元 remains fixed |
| Contract name and number | `tbl[0]/tr[24]/tc[1]/p[0]` | One line, may wrap |
| Lender / borrower | `tbl[0]/tr[25]/tc[1]/p[0..1]` | One organization/person per line |
| Agreed / disbursed amount | `tbl[0]/tr[26]/tc[1]/p[0..1]` | Amount plus fixed currency wording |
| Loan term and maturity state | `tbl[0]/tr[27]/tc[1]/p[0..1]` | Existing two-line slot; dates are normalized to Chinese date text |
| Effective interest rate | `tbl[0]/tr[28]/tc[1]/p[0]` | Rebuild only the rate line; preserve the remaining rate options |
| Disbursement date and amount | `tbl[0]/tr[29]/tc[1]/p[0]` | One compact sentence in the existing slot |
| Repayment method | `tbl[0]/tr[30]/tc[1]/p[0]` | Existing checkbox options reconstructed with exactly one matching option checked |
| Repayment history | `tbl[0]/tr[31]/tc[1]/p[0]` | Cumulative principal and latest repayment summary; may wrap inside the cell |
| Overdue state and days | `tbl[0]/tr[32]/tc[1]/p[0]` | Existing yes/no structure plus baseline overdue days |
| Guarantee method | `tbl[0]/tr[33]/tc[1]/p[0]` | Existing security row; credit loans mark “否” and keep the method text |

The retained template remains unchanged. Runtime output removes yellow run highlighting from the generated Word copy at the user's request; the browser preview alone marks every inserted value in yellow. The red “诉讼请求” heading highlight is preserve-only.

## Package preservation

- `word/document.xml`: editable only at the locators listed above.
- Styles, numbering, settings, theme, relationships, document properties, content types and every other part: preserve byte-for-byte.
- No content controls, text fields or bookmarks are available in the retained source.

## Fidelity gates

- Reference SHA-256 must match before every generation.
- Package part set must remain identical.
- All package parts except `word/document.xml` must remain byte-identical.
- Section count, page geometry, table structure, borders, direct formatting and all non-mapped prose remain unchanged.
- Default demonstration data should remain six pages; longer user content may add pagination but must not clip or overlap.
- Generated DOCX must be rendered through Microsoft Word (or the packaged renderer when LibreOffice is available) and every page inspected before release-quality delivery.
