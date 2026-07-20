const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const projectRoot = path.resolve(__dirname, "..");
const python = process.env.LEGAL_PYTHON || "python";
const edge = process.env.LEGAL_EDGE || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const port = Number(process.env.LEGAL_UI_PORT || 8766);

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForJson(url, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1000) });
      if (response.ok) return response.json();
    } catch (_) {
      // The local server is still starting.
    }
    await delay(150);
  }
  throw new Error(`Timed out waiting for ${url}`);
}

async function main() {
  const fullCaseText = fs.readFileSync(path.join(projectRoot, "examples", "full_case_328.txt"), "utf8");
  const server = spawn(python, ["server.py", "--no-browser", "--port", String(port)], {
    cwd: projectRoot,
    windowsHide: true,
    stdio: "ignore",
  });
  let browser;
  try {
    await waitForJson(`http://127.0.0.1:${port}/api/schema`);
    browser = await chromium.launch({ executablePath: edge, headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("[name=defendant_name]", { state: "attached" });

    const scroll = await page.evaluate(() => {
      const node = document.querySelector(".input-scroll");
      const before = { clientHeight: node.clientHeight, scrollHeight: node.scrollHeight };
      node.scrollTop = node.scrollHeight;
      return { ...before, scrollTop: node.scrollTop };
    });
    assert.ok(scroll.scrollHeight > scroll.clientHeight, JSON.stringify(scroll));
    assert.ok(scroll.scrollTop > 0, JSON.stringify(scroll));

    const screenshotText = "姓名：张三                          性别：男出生日期：2000.01.01         民族：汉 工作单位：宝鸡                          职务：无          联系电话：130945515        住所地（户籍所在地）：团结村                             证件类型：身份证证件号码：000000199004260012";
    const values = await page.evaluate((text) => {
      const paste = document.querySelector("#pasteInput");
      paste.value = text;
      document.querySelector("#recognizeButton").click();
      const names = [
        "defendant_name", "defendant_gender", "defendant_birth_date",
        "defendant_ethnicity", "defendant_employer", "defendant_title",
        "defendant_phone", "defendant_address", "defendant_id_number",
      ];
      return Object.fromEntries(names.map((name) => [
        name,
        document.querySelector(`[name=${name}]`).value,
      ]));
    }, screenshotText);
    assert.deepEqual(values, {
      defendant_name: "张三",
      defendant_gender: "男",
      defendant_birth_date: "2000-01-01",
      defendant_ethnicity: "汉",
      defendant_employer: "宝鸡",
      defendant_title: "无",
      defendant_phone: "130945515",
      defendant_address: "团结村",
      defendant_id_number: "000000199004260012",
    });

    await page.click("#loadExampleButton");
    const fullCase = await page.evaluate((text) => {
      const paste = document.querySelector("#pasteInput");
      paste.value = text;
      document.querySelector("#recognizeButton").click();
      const names = [
        "customer_number", "contract_number", "loan_account", "defendant_name",
        "defendant_birth_date", "defendant_education", "defendant_occupation",
        "defendant_annual_income", "marital_status", "defendant_gender",
        "service_address", "defendant_id_number", "defendant_address", "defendant_phone",
        "loan_type", "repayment_method", "loan_amount", "effective_interest_rate",
        "loan_disbursement_date", "loan_maturity_date", "overdue_days",
        "five_level_classification", "relationship_manager", "lending_org_full",
        "lending_org_uscc", "head_office_uscc", "loan_borrower", "loan_lender",
      ];
      return {
        values: Object.fromEntries(names.map((name) => [
          name,
          document.querySelector(`[name=${name}]`).value,
        ])),
        recognized: document.querySelector("#recognizeResult").textContent.trim(),
        groupCount: document.querySelectorAll(".form-group").length,
        fieldCount: document.querySelectorAll(".field-control").length,
        counters: [...document.querySelectorAll("[data-group-count]")].map((node) => node.textContent.trim()),
      };
    }, fullCaseText);
    assert.deepEqual(fullCase.values, {
      customer_number: "DEMO-CUSTOMER-001",
      contract_number: "DEMO00000000000001",
      loan_account: "00000000000000000000000001",
      defendant_name: "测试乙",
      defendant_birth_date: "1990-01-01",
      defendant_education: "本科",
      defendant_occupation: "民营企业",
      defendant_annual_income: "120000.00",
      marital_status: "已婚",
      defendant_gender: "男",
      service_address: "湖北省武汉市示例区测试路1号",
      defendant_id_number: "000000199004260012",
      defendant_address: "湖北省武汉市示例区测试路1号",
      defendant_phone: "19900000004",
      loan_type: "0217 - 建易贷（一次性还本付息）",
      repayment_method: "一次性还本付息",
      loan_amount: "270900.0",
      effective_interest_rate: "3.600000",
      loan_disbursement_date: "2023-07-26",
      loan_maturity_date: "2024-07-26",
      overdue_days: "190",
      five_level_classification: "可疑二级",
      relationship_manager: "测试经理",
      lending_org_full: "示例银行股份有限公司示例支行",
      lending_org_uscc: "DEMO-USCC-00000001",
      head_office_uscc: "DEMO-USCC-00000002",
      loan_borrower: "测试乙",
      loan_lender: "示例银行股份有限公司示例支行",
    });
    assert.match(fullCase.recognized, /已识别并填入 74 个字段/);
    assert.equal(fullCase.groupCount, 7);
    assert.equal(fullCase.fieldCount, 83);
    assert.equal(fullCase.counters.length, 7);

    const generationStarted = Date.now();
    await page.click("#generateButton");
    await page.waitForFunction(
      () => ["Word已生成", "生成失败"].includes(document.querySelector("#previewStatus").textContent.trim()),
      { timeout: 10000 },
    );
    const generation = await page.evaluate(() => ({
      status: document.querySelector("#previewStatus").textContent.trim(),
      toast: document.querySelector("#toast").hidden ? "" : document.querySelector("#toast").textContent.trim(),
      message: document.querySelector("#messagePanel").hidden ? "" : document.querySelector("#messagePanel").textContent.trim(),
      htmlHidden: document.querySelector("#htmlPreview").hidden,
      htmlDisplay: getComputedStyle(document.querySelector("#htmlPreview")).display,
      emptyDisplay: getComputedStyle(document.querySelector("#emptyPreview")).display,
      previewText: document.querySelector("#htmlPreview").textContent,
      previewTablePresent: Boolean(document.querySelector("#htmlPreview .word-document-table")),
      previewRowCount: document.querySelectorAll("#htmlPreview [data-word-row]").length,
      highlightedFillCount: document.querySelectorAll('#htmlPreview [data-preview-fill="true"]').length,
      fullIdInputValue: document.querySelector('[name="defendant_id_number"]').value,
      docxDisabled: document.querySelector("#downloadDocx").getAttribute("aria-disabled"),
      pdfButtonPresent: Boolean(document.querySelector("#downloadPdf")),
    }));
    generation.elapsedMs = Date.now() - generationStarted;

    assert.equal(generation.status, "Word已生成", JSON.stringify(generation));
    assert.equal(generation.htmlHidden, false, JSON.stringify(generation));
    assert.notEqual(generation.htmlDisplay, "none", JSON.stringify(generation));
    assert.equal(generation.emptyDisplay, "none", JSON.stringify(generation));
    assert.match(generation.previewText, /测试乙/);
    assert.match(generation.previewText, /280,?561\.03/);
    assert.match(generation.previewText, /000000199004260012/);
    assert.match(generation.previewText, /2023年7月26日起至2024年7月26日止/);
    assert.match(generation.previewText, /基准日已逾期190天/);
    assert.equal(generation.fullIdInputValue, "000000199004260012");
    assert.equal(generation.previewTablePresent, true, JSON.stringify(generation));
    assert.equal(generation.previewRowCount, 47, JSON.stringify(generation));
    assert.ok(generation.highlightedFillCount >= 20, JSON.stringify(generation));
    assert.equal(generation.docxDisabled, "false", JSON.stringify(generation));
    assert.equal(generation.pdfButtonPresent, false, JSON.stringify(generation));
    assert.ok(generation.elapsedMs < 5000, JSON.stringify(generation));

    const accessibility = await page.evaluate(() => {
      const controls = [...document.querySelectorAll(".field-control")];
      return {
        unlabeled: controls.filter((control) => !document.querySelector(`label[for="${control.id}"]`)).length,
        detailSummaries: document.querySelectorAll("details > summary").length,
      };
    });
    assert.equal(accessibility.unlabeled, 0, JSON.stringify(accessibility));
    assert.equal(accessibility.detailSummaries, 7, JSON.stringify(accessibility));

    fs.mkdirSync(path.join(projectRoot, "work"), { recursive: true });
    await page.screenshot({ path: path.join(projectRoot, "work", "ui-fixed.png"), fullPage: false });
    await page.locator('#htmlPreview [data-preview-fill="true"]').first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(projectRoot, "work", "ui-highlight-fixed.png"), fullPage: false });

    const responsive = [];
    for (const viewport of [{ width: 1024, height: 768 }, { width: 640, height: 900 }]) {
      const responsivePage = await browser.newPage({ viewport });
      await responsivePage.goto(`http://127.0.0.1:${port}/`, { waitUntil: "domcontentloaded" });
      await responsivePage.waitForSelector(".form-group", { state: "attached" });
      const result = await responsivePage.evaluate(() => {
        const input = document.querySelector(".input-pane").getBoundingClientRect();
        const preview = document.querySelector(".preview-pane").getBoundingClientRect();
        return {
          innerWidth: window.innerWidth,
          scrollWidth: document.documentElement.scrollWidth,
          stacked: preview.top >= input.bottom - 1,
        };
      });
      assert.ok(result.scrollWidth <= result.innerWidth + 1, JSON.stringify({ viewport, result }));
      assert.equal(result.stacked, true, JSON.stringify({ viewport, result }));
      responsive.push({ viewport, ...result });
      await responsivePage.close();
    }
    console.log(JSON.stringify({
      scroll,
      values,
      fullCase,
      generation,
      accessibility,
      responsive,
      screenshots: ["work/ui-fixed.png", "work/ui-highlight-fixed.png"],
    }, null, 2));
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
