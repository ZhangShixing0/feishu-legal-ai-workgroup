const state = {
  schema: null,
  defaults: {},
  generated: false,
  dirty: false,
  currentJob: null,
};

const APP_VERSION = "0.6.0";

const el = (id) => document.getElementById(id);
const formGroups = el("formGroups");
const generateButton = el("generateButton");
const validationSummary = el("validationSummary");
const previewStatus = el("previewStatus");
const emptyPreview = el("emptyPreview");
const loadingPreview = el("loadingPreview");
const htmlPreview = el("htmlPreview");
const messagePanel = el("messagePanel");
const downloadDocx = el("downloadDocx");
const saveState = el("saveState");
const recognizeResult = el("recognizeResult");

const { parsePastedText } = window.LegalTextParser;

function iconUse(id) {
  return `<svg aria-hidden="true"><use href="#${id}"></use></svg>`;
}

function applyDeploymentMode(result) {
  const serverMode = result.deployment_mode === "server";
  el("brandSubtitle").textContent = serverMode ? "律所内部文书生成工具" : "法律文书离线生成工具";
  el("privacyBadge").innerHTML = serverMode
    ? `${iconUse("icon-lock")}HTTPS · 服务器处理`
    : `${iconUse("icon-lock")}离线 · 仅本机处理`;
  el("processingNote").innerHTML = serverMode
    ? `${iconUse("icon-lock")}案件材料经加密连接发送至本服务器处理`
    : `${iconUse("icon-lock")}整个过程仅在本机完成`;
  el("loadingCopy").textContent = serverMode
    ? "正在服务器填充模板，不再等待PDF转换。"
    : "仅在本机填充模板，不再等待PDF转换。";
}

function createField(field) {
  const wrapper = document.createElement("div");
  wrapper.className = `form-field${field.wide ? " wide" : ""}`;
  const label = document.createElement("label");
  label.htmlFor = `field-${field.id}`;
  label.innerHTML = `${escapeHtml(field.label)}${field.required ? '<span class="required-mark" aria-label="必填">*</span>' : ""}`;
  wrapper.appendChild(label);

  let control;
  if (field.type === "select") {
    control = document.createElement("select");
    control.className = "field-control";
    for (const option of field.options || []) {
      const node = document.createElement("option");
      node.value = option;
      node.textContent = option;
      control.appendChild(node);
    }
  } else {
    control = document.createElement("input");
    control.type = field.type === "date" ? "date" : field.type === "tel" ? "tel" : "text";
    control.className = `field-control${field.type === "money" ? " money" : ""}`;
    if (field.type === "money") control.inputMode = "decimal";
    if (field.type === "tel") control.inputMode = "tel";
    if (field.type === "number") control.inputMode = "numeric";
  }
  control.id = `field-${field.id}`;
  control.name = field.id;
  control.dataset.label = field.label;
  control.dataset.required = field.required ? "true" : "false";
  control.autocomplete = "off";
  control.addEventListener("input", () => markDirty());
  control.addEventListener("change", () => markDirty());
  wrapper.appendChild(control);

  if (field.helper) {
    const helper = document.createElement("div");
    helper.className = "field-helper";
    helper.textContent = field.helper;
    wrapper.appendChild(helper);
  }

  const error = document.createElement("div");
  error.className = "field-error";
  error.id = `error-${field.id}`;
  error.setAttribute("aria-live", "polite");
  wrapper.appendChild(error);
  return wrapper;
}

function renderSchema(schema) {
  formGroups.innerHTML = "";
  schema.groups.forEach((group, index) => {
    const details = document.createElement("details");
    details.className = "form-group";
    details.open = index === 0;
    const summary = document.createElement("summary");
    summary.innerHTML = `
      <div>
        <div class="group-title">${escapeHtml(group.title)}</div>
        <div class="group-description">${escapeHtml(group.description || "")}</div>
      </div>
      <span class="group-count" data-group-count="${escapeHtml(group.id)}">0/${group.fields.length}</span>`;
    details.appendChild(summary);
    const grid = document.createElement("div");
    grid.className = "form-grid";
    group.fields.forEach((field) => grid.appendChild(createField(field)));
    details.appendChild(grid);
    formGroups.appendChild(details);
  });
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char]);
}

function setFields(values) {
  Object.entries(values).forEach(([key, value]) => {
    const input = document.querySelector(`[name="${CSS.escape(key)}"]`);
    if (input) input.value = value ?? "";
  });
  markDirty(false);
  validateClient();
}

function collectFields() {
  const result = {};
  document.querySelectorAll(".field-control").forEach((control) => {
    result[control.name] = control.value.trim();
  });
  return result;
}

function validateClient() {
  let missing = 0;
  let firstInvalid = null;
  document.querySelectorAll(".field-control").forEach((control) => {
    const error = el(`error-${control.name}`);
    const invalid = control.dataset.required === "true" && !control.value.trim();
    control.setAttribute("aria-invalid", invalid ? "true" : "false");
    if (error) error.textContent = invalid ? "请填写此项" : "";
    if (invalid) {
      missing += 1;
      if (!firstInvalid) firstInvalid = control;
    }
  });
  state.schema?.groups.forEach((group) => {
    const filled = group.fields.filter((field) => {
      const control = document.querySelector(`[name="${CSS.escape(field.id)}"]`);
      return Boolean(control?.value.trim());
    }).length;
    const counter = document.querySelector(`[data-group-count="${CSS.escape(group.id)}"]`);
    if (counter) counter.textContent = `${filled}/${group.fields.length}`;
  });
  if (missing) {
    validationSummary.textContent = `还有 ${missing} 个必填项未填写`;
    generateButton.disabled = true;
  } else {
    validationSummary.textContent = "必填信息完整，点击后才生成预览";
    generateButton.disabled = false;
  }
  return { valid: missing === 0, firstInvalid };
}

function markDirty(changed = true) {
  if (changed) state.dirty = true;
  saveState.textContent = changed ? "内容已修改" : "演示数据已载入";
  if (state.generated && changed) {
    previewStatus.textContent = "需重新生成";
    previewStatus.className = "preview-status is-stale";
  }
  validateClient();
}

function recognizePaste() {
  const text = el("pasteInput").value.trim();
  if (!text) {
    recognizeResult.textContent = "请先粘贴带有“字段名：内容”的文本。";
    return;
  }
  const values = parsePastedText(text, state.schema?.paste_aliases || {});
  const entries = Object.entries(values);
  if (!entries.length) {
    recognizeResult.textContent = "没有识别到可填写字段，请保留姓名、电话、合同编号等字段标签后重试。";
    return;
  }
  let applied = 0;
  entries.forEach(([key, value]) => {
    const input = document.querySelector(`[name="${CSS.escape(key)}"]`);
    if (input) {
      input.value = value;
      applied += 1;
    }
  });
  recognizeResult.textContent = `已识别并填入 ${applied} 个字段（含自动带入项）。Excel未提供的原告、基准日和罚息规则请重点核对。`;
  markDirty();
}

function showPreviewState(mode) {
  emptyPreview.hidden = mode !== "empty";
  loadingPreview.hidden = mode !== "loading";
  htmlPreview.hidden = mode !== "html";
}

function setDownloads(result) {
  downloadDocx.href = result.docx_url;
  downloadDocx.classList.remove("is-disabled");
  downloadDocx.setAttribute("aria-disabled", "false");
}

function showMessages(result) {
  const messages = [...(result.warnings || [])];
  if (!messages.length) {
    messagePanel.hidden = true;
    messagePanel.innerHTML = "";
    return;
  }
  messagePanel.innerHTML = `<strong>${iconUse("icon-alert")} 请核对以下提示</strong><ul>${messages.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
  messagePanel.hidden = false;
}

function renderHtmlPreview(previewHtml) {
  if (!previewHtml || typeof previewHtml !== "string") {
    throw new Error("服务器未返回完整Word版式预览");
  }
  htmlPreview.innerHTML = `
    <div class="preview-document-shell">
      <div class="preview-disclaimer">
        ${iconUse("icon-alert")}
        <span><strong>Word版式预览</strong>内容和表格取自同一份Word模板；网页中所有填充值标黄，下载的Word不带黄色底色。</span>
      </div>
      ${previewHtml}
    </div>`;
}

async function generatePreview() {
  const clientValidation = validateClient();
  if (!clientValidation.valid) {
    clientValidation.firstInvalid?.focus();
    showToast("请先补全必填信息。", true);
    return;
  }
  generateButton.disabled = true;
  generateButton.innerHTML = `<span class="spinner mini"></span><span>正在生成</span>`;
  previewStatus.textContent = "生成中";
  previewStatus.className = "preview-status";
  showPreviewState("loading");
  messagePanel.hidden = true;
  const fields = collectFields();
  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields }),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || "生成失败");

    state.generated = true;
    state.dirty = false;
    state.currentJob = result.job_id;
    setDownloads(result);
    showMessages(result);
    renderHtmlPreview(result.preview_html);
    showPreviewState("html");
    previewStatus.textContent = "Word已生成";
    previewStatus.className = "preview-status is-ready";
    saveState.textContent = "Word版式预览与当前内容一致";
    showToast("Word已生成，可立即下载。", false);
  } catch (error) {
    showPreviewState("empty");
    previewStatus.textContent = "生成失败";
    previewStatus.className = "preview-status is-stale";
    messagePanel.hidden = false;
    messagePanel.innerHTML = `<strong>${iconUse("icon-alert")} 生成失败</strong><div>${escapeHtml(error.message || "生成失败，请重试。")}</div>`;
    showToast(error.message || "生成失败，请重试。", true);
  } finally {
    generateButton.innerHTML = `${iconUse("icon-eye")}<span>生成预览</span>`;
    validateClient();
  }
}

let toastTimer;
function showToast(message, isError) {
  const toast = el("toast");
  toast.textContent = message;
  toast.className = `toast${isError ? " is-error" : ""}`;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 4200);
}

async function initialize() {
  try {
    const response = await fetch("/api/schema", { cache: "no-store" });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || "无法载入字段配置");
    if (result.app_version !== APP_VERSION) {
      throw new Error("检测到仍在运行的旧版服务。请关闭之前启动的黑色命令窗口，再重新双击 run.bat。");
    }
    el("versionBadge").textContent = `验证版 ${APP_VERSION}`;
    applyDeploymentMode(result);
    state.schema = result.schema;
    state.defaults = result.schema.defaults;
    renderSchema(result.schema);
    setFields(state.defaults);
    validationSummary.textContent = "必填信息完整，点击后才生成预览";
    generateButton.disabled = false;
  } catch (error) {
    formGroups.innerHTML = `<div class="message-panel"><strong>字段配置载入失败</strong>${escapeHtml(error.message)}</div>`;
    validationSummary.textContent = "请重启本地服务后再试";
    saveState.textContent = "服务版本异常";
    showToast(error.message, true);
  }
}

el("recognizeButton").addEventListener("click", recognizePaste);
el("loadExampleButton").addEventListener("click", () => {
  setFields(state.defaults);
  recognizeResult.textContent = "已恢复演示数据。";
});
el("clearButton").addEventListener("click", () => {
  document.querySelectorAll(".field-control").forEach((control) => { control.value = ""; });
  el("pasteInput").value = "";
  recognizeResult.textContent = "已清空录入内容。";
  markDirty();
});
generateButton.addEventListener("click", generatePreview);

initialize();
