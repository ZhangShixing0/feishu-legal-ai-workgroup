const state = {
  jobId: null,
  fields: [],
  filter: "all",
  search: "",
  polling: false,
  lastProgress: null,
};

const elements = {
  form: document.querySelector("#uploadForm"),
  archiveInput: document.querySelector("#archiveInput"),
  workbookInput: document.querySelector("#workbookInput"),
  archiveZone: document.querySelector("#archiveZone"),
  workbookZone: document.querySelector("#workbookZone"),
  archiveHint: document.querySelector("#archiveHint"),
  workbookHint: document.querySelector("#workbookHint"),
  archiveState: document.querySelector("#archiveState"),
  workbookState: document.querySelector("#workbookState"),
  analyzeButton: document.querySelector("#analyzeButton"),
  exportButton: document.querySelector("#exportButton"),
  progressPanel: document.querySelector("#progressPanel"),
  progressTitle: document.querySelector("#progressTitle"),
  progressDetail: document.querySelector("#progressDetail"),
  progressBar: document.querySelector("#progressBar"),
  progressPercent: document.querySelector("#progressPercent"),
  progressSteps: [...document.querySelectorAll(".progress-steps li")],
  results: document.querySelector("#resultsSection"),
  formError: document.querySelector("#formError"),
  fieldBody: document.querySelector("#fieldTableBody"),
  fieldSearch: document.querySelector("#fieldSearch"),
  filterButtons: [...document.querySelectorAll(".filter-button")],
  emptyFilter: document.querySelector("#emptyFilter"),
  warningPanel: document.querySelector("#warningPanel"),
  warningList: document.querySelector("#warningList"),
  foundCount: document.querySelector("#foundCount"),
  missingCount: document.querySelector("#missingCount"),
  reviewCount: document.querySelector("#reviewCount"),
  fileCount: document.querySelector("#fileCount"),
  fileCountLabel: document.querySelector("#fileCountLabel"),
  targetHint: document.querySelector("#targetHint"),
  toast: document.querySelector("#toast"),
};

function formatBytes(size) {
  if (size < 1024) return `${size} B`;
  const units = ["KB", "MB", "GB"];
  let value = size / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(value >= 10 ? 1 : 2)} ${units[index]}`;
}

function setFileState(input, zone, hint, badge) {
  const file = input.files?.[0];
  zone.classList.toggle("has-file", Boolean(file));
  if (!file) {
    badge.textContent = "未选择";
    return;
  }
  hint.textContent = `${file.name} · ${formatBytes(file.size)}`;
  badge.textContent = "已选择";
}

function setupDropZone(input, zone, hint, badge) {
  input.addEventListener("change", () => setFileState(input, zone, hint, badge));
  ["dragenter", "dragover"].forEach((eventName) => {
    zone.addEventListener(eventName, (event) => {
      event.preventDefault();
      zone.classList.add("drag-over");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    zone.addEventListener(eventName, (event) => {
      event.preventDefault();
      zone.classList.remove("drag-over");
    });
  });
  zone.addEventListener("drop", (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (!file) return;
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    setFileState(input, zone, hint, badge);
  });
}

setupDropZone(elements.archiveInput, elements.archiveZone, elements.archiveHint, elements.archiveState);
setupDropZone(elements.workbookInput, elements.workbookZone, elements.workbookHint, elements.workbookState);

function showError(message) {
  elements.formError.textContent = message;
  elements.formError.hidden = false;
  elements.formError.focus?.();
}

function clearError() {
  elements.formError.hidden = true;
  elements.formError.textContent = "";
}

function renderProgress(progress = {}) {
  const phaseSteps = {
    queued: 0,
    preparing: 0,
    extract: 0,
    read: 1,
    ocr: 1,
    match: 2,
    complete: 3,
    failed: 3,
  };
  const percent = Math.max(0, Math.min(100, Number(progress.percent) || 0));
  const activeStep = phaseSteps[progress.phase] ?? 0;
  state.lastProgress = { ...progress, percent };
  elements.progressTitle.textContent = progress.title || "正在创建后台任务";
  elements.progressDetail.textContent = progress.detail || "上传完成后会在服务器后台处理";
  elements.progressPercent.textContent = `${Math.round(percent)}%`;
  elements.progressBar.style.width = `${percent}%`;
  const track = elements.progressBar.parentElement;
  track.setAttribute("aria-valuenow", String(Math.round(percent)));
  elements.progressSteps.forEach((step, stepIndex) => {
    step.classList.toggle("active", stepIndex <= activeStep);
    step.classList.toggle("current", stepIndex === activeStep);
  });
}

function startProgress(title = "正在上传并创建任务", detail = "请保持页面打开；短暂断线后可自动恢复") {
  elements.progressPanel.hidden = false;
  renderProgress({ phase: "queued", title, detail, percent: 1 });
}

function stopProgress() {
  elements.progressPanel.hidden = true;
  state.lastProgress = null;
}

function statusFor(field) {
  if (field.missing) return { label: "缺失标黄", className: "missing" };
  if (field.conflict) return { label: "值有冲突", className: "review" };
  if (field.preserved) return { label: "Excel保留", className: "preserved" };
  if (field.confidence < 0.85) return { label: "需核对", className: "review" };
  if (field.confidence < 0.93) return { label: "已识别", className: "medium" };
  return { label: "高置信", className: "high" };
}

function visibleFields() {
  const search = state.search.trim().toLowerCase();
  return state.fields.filter((field) => {
    const matchesFilter = state.filter === "all"
      || (state.filter === "missing" && field.missing)
      || (state.filter === "review" && !field.missing && field.confidence < 0.85)
      || (state.filter === "preserved" && field.preserved);
    if (!matchesFilter) return false;
    if (!search) return true;
    return [field.header, field.value, field.source, field.method, field.column]
      .some((value) => String(value ?? "").toLowerCase().includes(search));
  });
}

function renderFields() {
  elements.fieldBody.replaceChildren();
  const fields = visibleFields();
  fields.forEach((field) => {
    const row = document.createElement("tr");
    if (field.missing) row.classList.add("missing");
    else if (field.confidence < 0.85) row.classList.add("review-row");
    else if (field.preserved) row.classList.add("preserved-row");

    const columnCell = document.createElement("td");
    columnCell.textContent = field.column;

    const headerCell = document.createElement("td");
    headerCell.textContent = field.header;

    const valueCell = document.createElement("td");
    const input = document.createElement("input");
    input.className = "field-input";
    input.type = "text";
    input.value = field.value;
    input.setAttribute("aria-label", `${field.header}的提取值`);
    input.addEventListener("change", () => {
      field.value = input.value.trim() || "无";
      input.value = field.value;
      field.missing = field.value === "无";
      delete field.preserved;
      delete field.conflict;
      if (!field.missing) {
        field.confidence = 1;
        field.method = "人工修改";
        field.source = "网页人工核对";
      }
      renderFields();
      updateSummary();
    });
    valueCell.append(input);

    const statusCell = document.createElement("td");
    const status = statusFor(field);
    const badge = document.createElement("span");
    badge.className = `status-badge ${status.className}`;
    badge.textContent = status.label;
    statusCell.append(badge);

    const sourceCell = document.createElement("td");
    sourceCell.className = "source-cell";
    const method = document.createElement("strong");
    method.textContent = field.method;
    const source = document.createElement("span");
    source.textContent = field.source;
    sourceCell.append(method, source);
    row.append(columnCell, headerCell, valueCell, statusCell, sourceCell);
    elements.fieldBody.append(row);
  });
  elements.emptyFilter.hidden = fields.length > 0;
}

function updateSummary(summary = null) {
  const found = state.fields.filter((field) => !field.missing).length;
  const missing = state.fields.length - found;
  const review = state.fields.filter((field) => !field.missing && field.confidence < 0.85).length;
  elements.foundCount.textContent = found;
  elements.missingCount.textContent = missing;
  elements.reviewCount.textContent = review;
  if (summary) {
    elements.fileCount.textContent = summary.readable_files ?? summary.files;
    const total = summary.files_total ?? summary.files;
    elements.fileCountLabel.textContent = `个来源 / 共 ${total} 个文件`;
  }
}

function renderWarnings(warnings) {
  elements.warningList.replaceChildren();
  elements.warningPanel.hidden = !warnings?.length;
  (warnings || []).slice(0, 12).forEach((warning) => {
    const item = document.createElement("li");
    item.textContent = warning;
    elements.warningList.append(item);
  });
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.hidden = false;
  window.setTimeout(() => { elements.toast.hidden = true; }, 5000);
}

const JOB_STORAGE_KEY = "caseExtractorActiveJob";

function setAnalyzeBusy(busy) {
  elements.analyzeButton.disabled = busy;
  const label = elements.analyzeButton.querySelector("span");
  if (label) label.textContent = busy ? "后台处理中…" : "开始提取";
}

function wait(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = {};
  }
  if (!response.ok || !payload.ok) {
    const error = new Error(payload.error || `服务器返回 ${response.status}，请稍后重试。`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

async function pollAnalysis(jobId) {
  let transientFailures = 0;
  while (state.jobId === jobId) {
    try {
      const payload = await fetchJson(`api/jobs/${jobId}`, { cache: "no-store" });
      transientFailures = 0;
      if (payload.progress) renderProgress(payload.progress);
      if (payload.status === "completed" && payload.result) {
        sessionStorage.removeItem(JOB_STORAGE_KEY);
        return payload.result;
      }
      if (payload.status === "failed") {
        sessionStorage.removeItem(JOB_STORAGE_KEY);
        const failure = new Error(payload.error || "材料处理失败，请重新上传。");
        failure.permanent = true;
        throw failure;
      }
      await wait(payload.status === "queued" ? 2200 : 1500);
    } catch (error) {
      if (error.status === 404) sessionStorage.removeItem(JOB_STORAGE_KEY);
      if (error.status || error.permanent) throw error;
      transientFailures += 1;
      if (transientFailures >= 5) {
        throw new Error("与服务器的进度连接暂时中断。后台任务可能仍在运行，请刷新页面恢复进度。");
      }
      const last = state.lastProgress || { phase: "queued", percent: 1 };
      renderProgress({
        ...last,
        title: "连接暂时中断，正在自动重试",
        detail: `第 ${transientFailures}/5 次重试；后台任务不会重复启动`,
      });
      await wait(2500);
    }
  }
  throw new Error("任务已被新的分析替换。");
}

function applyAnalysis(payload) {
  state.jobId = payload.job_id;
  state.fields = payload.fields;
  state.filter = "all";
  state.search = "";
  elements.fieldSearch.value = "";
  elements.filterButtons.forEach((button) => button.classList.toggle("active", button.dataset.filter === "all"));
  updateSummary(payload.summary);
  renderWarnings(payload.warnings);
  renderFields();
  elements.results.hidden = false;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  elements.results.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  const readable = payload.summary.readable_files ?? payload.summary.files;
  const preserved = payload.summary.preserved || 0;
  showToast(`已解析 ${readable} 个材料来源，保留 ${preserved} 个Excel已有值；请核对黄色和冲突字段。`);
}

async function runQueuedJob(jobId) {
  state.jobId = jobId;
  state.polling = true;
  sessionStorage.setItem(JOB_STORAGE_KEY, jobId);
  const analysis = await pollAnalysis(jobId);
  applyAnalysis(analysis);
}

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();
  if (state.polling) {
    showError("已有任务正在后台处理，请等待当前任务完成。");
    return;
  }
  const archive = elements.archiveInput.files?.[0];
  const workbook = elements.workbookInput.files?.[0];
  if (!archive || !archive.name.toLowerCase().endsWith(".zip")) {
    showError("请选择一个 .zip 案件材料压缩包。");
    elements.archiveInput.focus();
    return;
  }
  if (!workbook || !workbook.name.toLowerCase().endsWith(".xlsx")) {
    showError("请选择一个 .xlsx 案件信息表。");
    elements.workbookInput.focus();
    return;
  }
  const formData = new FormData();
  formData.append("archive", archive);
  formData.append("workbook", workbook);
  setAnalyzeBusy(true);
  elements.results.hidden = true;
  startProgress();
  try {
    const accepted = await fetchJson("api/analyze", { method: "POST", body: formData });
    if (accepted.duplicate) showToast(accepted.message);
    await runQueuedJob(accepted.job_id);
  } catch (error) {
    showError(error.message || "材料处理失败，请重试。");
  } finally {
    state.polling = false;
    stopProgress();
    setAnalyzeBusy(false);
  }
});

elements.fieldSearch.addEventListener("input", () => {
  state.search = elements.fieldSearch.value;
  renderFields();
});

elements.filterButtons.forEach((button) => {
  button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    elements.filterButtons.forEach((item) => item.classList.toggle("active", item === button));
    renderFields();
  });
});

elements.exportButton.addEventListener("click", async () => {
  if (!state.jobId || !state.fields.length) return;
  elements.exportButton.disabled = true;
  const label = elements.exportButton.querySelector("span");
  const originalLabel = label.textContent;
  label.textContent = "正在生成 Excel…";
  try {
    const response = await fetch(`api/export/${state.jobId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields: state.fields }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "Excel 生成失败。");
    elements.targetHint.textContent = payload.matched_existing
      ? `已更新同案行：${payload.target}；${payload.missing_cells} 个“无”字段已标黄。`
      : `已新增案件行：${payload.target}；${payload.missing_cells} 个“无”字段已标黄。`;
    showToast("Excel 已生成，浏览器将开始下载。");
    window.location.assign(payload.download_url);
  } catch (error) {
    showError(error.message || "Excel 生成失败，请重试。");
    window.scrollTo({ top: 0, behavior: "smooth" });
  } finally {
    elements.exportButton.disabled = false;
    label.textContent = originalLabel;
  }
});

async function resumePendingJob() {
  const queryJobId = new URLSearchParams(window.location.search).get("job_id");
  const jobId = queryJobId || sessionStorage.getItem(JOB_STORAGE_KEY);
  if (!jobId) return;
  if (queryJobId) sessionStorage.setItem(JOB_STORAGE_KEY, queryJobId);
  clearError();
  state.jobId = jobId;
  state.polling = true;
  setAnalyzeBusy(true);
  elements.results.hidden = true;
  startProgress("正在恢复后台任务", "页面刷新不会重复启动OCR，正在读取服务器进度");
  try {
    const analysis = await pollAnalysis(jobId);
    applyAnalysis(analysis);
  } catch (error) {
    if (error.status === 404) sessionStorage.removeItem(JOB_STORAGE_KEY);
    showError(error.message || "无法恢复后台任务，请重新上传材料。");
  } finally {
    state.polling = false;
    stopProgress();
    setAnalyzeBusy(false);
  }
}

resumePendingJob();
