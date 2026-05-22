const state = {
  meta: null,
  catalog: null,
  filter: "all",
  search: "",
  alphas: [],
  selectedAlphaId: null,
  nextAlphaNumber: 1,
  batch: null,
  correlation: {
    alphaAId: null,
    alphaBId: null,
    status: "idle",
    progress: 0,
    message: "等待计算。",
    result: null,
    error: null,
    jobId: null,
    runToken: null,
  },
};

const nodes = {
  addAlphaButton: document.getElementById("add-alpha"),
  runAllButton: document.getElementById("run-all-backtests"),
  windowCount: document.getElementById("window-count"),
  alphaTabList: document.getElementById("alpha-tab-list"),
  alphaEditorShell: document.getElementById("alpha-editor-shell"),
  batchStatusText: document.getElementById("batch-status-text"),
  batchProgressBar: document.getElementById("batch-progress-bar"),
  runCorrelationButton: document.getElementById("run-correlation"),
  correlationAlphaA: document.getElementById("correlation-alpha-a"),
  correlationAlphaB: document.getElementById("correlation-alpha-b"),
  correlationStatusText: document.getElementById("correlation-status-text"),
  correlationProgressBar: document.getElementById("correlation-progress-bar"),
  correlationSummaryGrid: document.getElementById("correlation-summary-grid"),
  correlationChart: document.getElementById("correlation-chart"),
  correlationCaption: document.getElementById("correlation-caption"),
  correlationYearlyTableBody: document.getElementById("correlation-yearly-table-body"),
  correlationDebugGrid: document.getElementById("correlation-debug-grid"),
  fixedRulesList: document.getElementById("fixed-rules-list"),
  exampleButtons: document.getElementById("example-buttons"),
  selectedAlphaName: document.getElementById("selected-alpha-name"),
  selectedAlphaExpression: document.getElementById("selected-alpha-expression"),
  selectedAlphaStatus: document.getElementById("selected-alpha-status"),
  summaryGrid: document.getElementById("summary-grid"),
  equityChart: document.getElementById("equity-chart"),
  drawdownChart: document.getElementById("drawdown-chart"),
  icChart: document.getElementById("ic-chart"),
  equityCaption: document.getElementById("equity-caption"),
  drawdownCaption: document.getElementById("drawdown-caption"),
  icCaption: document.getElementById("ic-caption"),
  yearlyTableBody: document.getElementById("yearly-table-body"),
  debugGrid: document.getElementById("debug-grid"),
  rangeBadge: document.getElementById("range-badge"),
  rulesHelpList: document.getElementById("rules-help-list"),
  catalogSearch: document.getElementById("catalog-search"),
  catalogList: document.getElementById("catalog-list"),
  filterButtons: Array.from(document.querySelectorAll(".filter-button")),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function formatPercent(value) {
  return `${(Number(value || 0) * 100).toFixed(2)}%`;
}

function formatNumber(value, digits = 2) {
  return Number(value || 0).toFixed(digits);
}

function formatBps(value) {
  return `${formatNumber(value, 2)} bps`;
}

function formatSummaryValue(key, value) {
  const percentKeys = new Set([
    "totalReturn",
    "annualizedReturn",
    "maxDrawdown",
    "averageTurnover",
    "turnover",
    "coverage",
  ]);
  if (key === "margin") {
    return formatBps(value);
  }
  if (key === "ic") {
    return formatNumber(value, 4);
  }
  if (key === "icir") {
    return formatNumber(value, 2);
  }
  if (percentKeys.has(key)) {
    return formatPercent(value);
  }
  return formatNumber(value, key === "sharpe" ? 2 : 2);
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function generateAlphaId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return `alpha-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function getSelectedAlpha() {
  return state.alphas.find((item) => item.id === state.selectedAlphaId) || null;
}

function findAlpha(alphaId) {
  return state.alphas.find((item) => item.id === alphaId) || null;
}

function hasLiveJobs() {
  return state.alphas.some((item) => item.status === "queued" || item.status === "running");
}

function isBatchActive() {
  if (!state.batch) {
    return false;
  }
  const snapshot = batchSnapshot();
  return Boolean(snapshot && snapshot.total > 0 && snapshot.completed < snapshot.total);
}

function alphaStatusLabel(status) {
  if (status === "queued") return "排队中";
  if (status === "running") return "回测中";
  if (status === "succeeded") return "已完成";
  if (status === "failed") return "失败";
  return "待运行";
}

function correlationStatusLabel(status) {
  if (status === "queued") return "排队中";
  if (status === "running") return "计算中";
  if (status === "succeeded") return "已完成";
  if (status === "failed") return "失败";
  return "待计算";
}

function isCorrelationRunning() {
  return state.correlation.status === "queued" || state.correlation.status === "running";
}

function invalidateCorrelationResultIfUsesAlpha(alphaId) {
  if (!state.correlation.result || isCorrelationRunning()) {
    return;
  }
  if (state.correlation.alphaAId !== alphaId && state.correlation.alphaBId !== alphaId) {
    return;
  }
  state.correlation.result = null;
  state.correlation.error = null;
  state.correlation.jobId = null;
  state.correlation.status = "idle";
  state.correlation.progress = 0;
  state.correlation.message = "相关因子已修改，请重新计算。";
}

function updateBatchCard(message, progress = 0) {
  nodes.batchStatusText.textContent = message;
  nodes.batchProgressBar.style.width = `${clamp(progress, 0, 1) * 100}%`;
}

function configuredParallelLimit() {
  const fallback = state.meta?.parallelLimitDefault || 5;
  const max = state.meta?.parallelLimitMax || fallback;
  return clamp(fallback, 1, max);
}

function sanitizeDecayValue(value) {
  const raw = Number(value);
  if (!Number.isFinite(raw)) {
    return 1;
  }
  return Math.max(1, Math.round(raw));
}

function defaultStartDate() {
  return state.meta?.dateRange?.start || window.APP_DEFAULTS.startDate || "";
}

function defaultEndDate() {
  return state.meta?.dateRange?.end || window.APP_DEFAULTS.endDate || "";
}

function sanitizeAlphaDate(value, fallback) {
  const next = String(value || "").trim();
  if (!next) {
    return fallback;
  }
  return next;
}

function createAlphaWindow(expression = "", options = {}) {
  const alpha = {
    id: generateAlphaId(),
    title: `因子 ${state.nextAlphaNumber}`,
    expression,
    startDate: sanitizeAlphaDate(options.startDate, defaultStartDate()),
    endDate: sanitizeAlphaDate(options.endDate, defaultEndDate()),
    decay: sanitizeDecayValue(options.decay ?? 1),
    status: "idle",
    progress: 0,
    message: "等待运行。",
    result: null,
    error: null,
    jobId: null,
    runToken: null,
  };
  state.nextAlphaNumber += 1;
  state.alphas.push(alpha);
  if (options.select !== false) {
    state.selectedAlphaId = alpha.id;
  }
  renderAlphaWindows();
  renderSelectedAlphaResult();
}

function removeAlphaWindow(alphaId) {
  if (state.alphas.length <= 1 || hasLiveJobs()) {
    return;
  }
  state.alphas = state.alphas.filter((item) => item.id !== alphaId);
  if (state.correlation.alphaAId === alphaId || state.correlation.alphaBId === alphaId) {
    state.correlation.result = null;
    state.correlation.error = null;
    state.correlation.jobId = null;
    state.correlation.status = "idle";
    state.correlation.progress = 0;
    state.correlation.message = "因子窗口已变化，请重新计算相关性。";
  }
  if (state.selectedAlphaId === alphaId) {
    state.selectedAlphaId = state.alphas[0]?.id || null;
  }
  renderAlphaWindows();
  renderSelectedAlphaResult();
}

function selectAlpha(alphaId) {
  state.selectedAlphaId = alphaId;
  renderAlphaWindows();
  renderSelectedAlphaResult();
}

function setAlphaExpression(alphaId, expression) {
  const alpha = findAlpha(alphaId);
  if (!alpha) {
    return;
  }
  alpha.expression = expression;
  invalidateCorrelationResultIfUsesAlpha(alphaId);
  if (state.selectedAlphaId === alphaId) {
    nodes.selectedAlphaExpression.textContent = expression.trim()
      ? expression.trim()
      : "当前窗口还没有输入表达式。";
  }
}

function setAlphaDecay(alphaId, decay) {
  const alpha = findAlpha(alphaId);
  if (!alpha) {
    return 1;
  }
  alpha.decay = sanitizeDecayValue(decay);
  invalidateCorrelationResultIfUsesAlpha(alphaId);
  return alpha.decay;
}

function setAlphaDate(alphaId, key, value) {
  const alpha = findAlpha(alphaId);
  if (!alpha) {
    return "";
  }
  if (key !== "startDate" && key !== "endDate") {
    return "";
  }
  const fallback = key === "startDate" ? defaultStartDate() : defaultEndDate();
  alpha[key] = sanitizeAlphaDate(value, fallback);
  invalidateCorrelationResultIfUsesAlpha(alphaId);
  return alpha[key];
}

function prepareAlphaForRun(alphaId) {
  const alpha = findAlpha(alphaId);
  if (!alpha) {
    return null;
  }
  alpha.status = "queued";
  alpha.progress = 0;
  alpha.message = "等待提交任务。";
  alpha.result = null;
  alpha.error = null;
  alpha.jobId = null;
  alpha.runToken = generateAlphaId();
  return alpha;
}

function setAlphaSkipped(alphaId, message) {
  const alpha = findAlpha(alphaId);
  if (!alpha) {
    return;
  }
  alpha.status = "idle";
  alpha.progress = 0;
  alpha.message = message;
  alpha.error = null;
  alpha.jobId = null;
  renderAlphaWindows();
  renderSelectedAlphaResult();
}

function renderFixedRules() {
  if (!state.meta) return;
  nodes.fixedRulesList.innerHTML = "";
  state.meta.fixedRules.forEach((item) => {
    const tag = document.createElement("span");
    tag.textContent = item;
    nodes.fixedRulesList.appendChild(tag);
  });
}

function ensureSelectedAlpha() {
  if (!state.selectedAlphaId && state.alphas.length) {
    state.selectedAlphaId = state.alphas[0].id;
  }
}

function renderExamples() {
  if (!state.meta) return;
  nodes.exampleButtons.innerHTML = "";
  state.meta.exampleExpressions.forEach((item) => {
    const button = document.createElement("button");
    button.className = "example-button";
    button.type = "button";
    button.textContent = item.label;
    button.addEventListener("click", () => {
      ensureSelectedAlpha();
      const alpha = getSelectedAlpha();
      if (!alpha || alpha.status === "queued" || alpha.status === "running") {
        createAlphaWindow(item.expression);
        return;
      }
      alpha.expression = item.expression;
      renderAlphaEditor();
      renderSelectedAlphaResult();
    });
    nodes.exampleButtons.appendChild(button);
  });
}

function buildCatalogItems() {
  if (!state.catalog) return [];
  return [
    ...state.catalog.rawFields.map((item) => ({ ...item, category: "raw" })),
    ...state.catalog.derivedFields.map((item) => ({ ...item, category: "derived" })),
    ...state.catalog.functions.map((item) => ({ ...item, category: "function" })),
  ];
}

function renderRulesHelp() {
  if (!state.catalog?.backtestRules) return;
  nodes.rulesHelpList.innerHTML = "";
  state.catalog.backtestRules.forEach((section) => {
    const card = document.createElement("article");
    card.className = "rule-card";
    card.innerHTML = `
      <h3>${escapeHtml(section.title)}</h3>
      <ul>
        ${section.items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    `;
    nodes.rulesHelpList.appendChild(card);
  });
}

function categoryLabel(category) {
  if (category === "raw") return "原始字段";
  if (category === "derived") return "派生字段";
  if (category === "function") return "函数";
  return category;
}

function renderCatalog() {
  const query = state.search.trim().toLowerCase();
  const items = buildCatalogItems().filter((item) => {
    const matchesFilter = state.filter === "all" || item.category === state.filter;
    if (!matchesFilter) return false;
    if (!query) return true;
    return Object.values(item)
      .join(" ")
      .toLowerCase()
      .includes(query);
  });

  nodes.catalogList.innerHTML = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "catalog-card";
    empty.innerHTML = "<p>没有匹配项。</p>";
    nodes.catalogList.appendChild(empty);
    return;
  }

  items.forEach((item) => {
    const card = document.createElement("article");
    card.className = "catalog-card";
    const title = item.signature || item.name;
    const description = item.description || item.notes || "";
    const noteBits = [];
    if (item.dimensions) {
      noteBits.push(
        `<div><strong>维度</strong><span>${escapeHtml(item.dimensions)}</span></div>`
      );
    }
    if (item.definition) {
      noteBits.push(
        `<div><strong>定义</strong><code>${escapeHtml(item.definition)}</code></div>`
      );
    }
    if (item.returns) {
      noteBits.push(`<div><strong>返回</strong><span>${escapeHtml(item.returns)}</span></div>`);
    }
    if (item.notes && item.category !== "raw") {
      noteBits.push(`<div><strong>备注</strong><span>${escapeHtml(item.notes)}</span></div>`);
    }
    if (item.example) {
      noteBits.push(`<div><strong>示例</strong><code>${escapeHtml(item.example)}</code></div>`);
    }
    if (item.expressionReady && item.category === "raw") {
      noteBits.push(
        `<div><strong>表达式</strong><span>${
          item.expressionReady === "true" ? "可直接使用" : "仅查询展示"
        }</span></div>`
      );
    }

    card.innerHTML = `
      <div class="topline">
        <div>
          <h3>${escapeHtml(item.displayName || item.name)}</h3>
          <code>${escapeHtml(title)}</code>
        </div>
        <span class="tag">${escapeHtml(categoryLabel(item.category))}</span>
      </div>
      <p>${escapeHtml(description)}</p>
      <div class="metadata-grid">
        ${noteBits.join("")}
      </div>
    `;
    nodes.catalogList.appendChild(card);
  });
}

function ensureCorrelationSelection() {
  const alphaIds = state.alphas.map((item) => item.id);
  if (!alphaIds.length) {
    state.correlation.alphaAId = null;
    state.correlation.alphaBId = null;
    return;
  }
  if (!alphaIds.includes(state.correlation.alphaAId)) {
    state.correlation.alphaAId = alphaIds[0] || null;
  }
  if (
    !alphaIds.includes(state.correlation.alphaBId) ||
    state.correlation.alphaBId === state.correlation.alphaAId
  ) {
    state.correlation.alphaBId =
      alphaIds.find((item) => item !== state.correlation.alphaAId) || null;
  }
}

function setCorrelationSelection(slot, alphaId) {
  if (slot !== "A" && slot !== "B") {
    return;
  }
  const key = slot === "A" ? "alphaAId" : "alphaBId";
  const otherKey = slot === "A" ? "alphaBId" : "alphaAId";
  state.correlation[key] = alphaId || null;
  if (state.correlation[key] === state.correlation[otherKey]) {
    state.correlation[otherKey] =
      state.alphas.find((item) => item.id !== state.correlation[key])?.id || null;
  }
  state.correlation.result = null;
  state.correlation.error = null;
  state.correlation.jobId = null;
  state.correlation.status = "idle";
  state.correlation.progress = 0;
  state.correlation.message = "因子组合已更改，请重新计算。";
}

function renderCorrelationSelector(selectNode, selectedId) {
  const existingValue = selectedId || "";
  selectNode.innerHTML = "";
  state.alphas.forEach((alpha) => {
    const option = document.createElement("option");
    option.value = alpha.id;
    option.textContent = alpha.title;
    selectNode.appendChild(option);
  });
  selectNode.value = existingValue;
}

function formatCorrelationValue(key, value) {
  if (key === "positiveRatio") {
    return formatPercent(value);
  }
  if (key === "averageSampleCount" || key === "validDays" || key === "totalDays") {
    return formatNumber(value, 0);
  }
  if (key === "correlationRatio") {
    return formatNumber(value, 2);
  }
  return formatNumber(value, 4);
}

function renderCorrelationSummary(result) {
  const items = [
    ["averageCorrelation", "平均相关性/Average Correlation", "按日横截面相关性的平均值"],
    ["correlationStd", "相关性标准差/Correlation Std", "每日相关性的标准差"],
    ["correlationRatio", "相关性比率/Correlation Ratio", "平均相关性除以相关性标准差"],
    ["positiveRatio", "正相关占比/Positive Ratio", "相关性大于 0 的日期占比"],
    ["averageSampleCount", "平均样本数/Average Sample Count", "每日参与相关性计算的股票数"],
    ["validDays", "有效天数/Valid Days", "成功算出相关性的日期数量"],
  ];
  nodes.correlationSummaryGrid.innerHTML = "";
  items.forEach(([key, label, hint]) => {
    const card = document.createElement("div");
    card.className = "summary-card";
    card.innerHTML = `
      <span class="label">${label}</span>
      <span class="value">${formatCorrelationValue(key, result.summary[key])}</span>
      <span class="hint">${hint}</span>
    `;
    nodes.correlationSummaryGrid.appendChild(card);
  });
}

function renderCorrelationSummaryPlaceholder(message) {
  nodes.correlationSummaryGrid.innerHTML = `<div class="summary-placeholder">${escapeHtml(message)}</div>`;
}

function renderCorrelationYearlyStats(rows) {
  if (!rows || !rows.length) {
    nodes.correlationYearlyTableBody.innerHTML =
      '<tr><td colspan="6" class="placeholder-row">暂无相关性统计。</td></tr>';
    return;
  }
  nodes.correlationYearlyTableBody.innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(row.year)}</td>
          <td>${formatNumber(row.averageCorrelation, 4)}</td>
          <td>${formatNumber(row.correlationStd, 4)}</td>
          <td>${formatNumber(row.correlationRatio, 2)}</td>
          <td>${formatNumber(row.averageSampleCount, 0)}</td>
          <td>${formatNumber(row.validDays, 0)}</td>
        </tr>
      `
    )
    .join("");
}

function renderCorrelationDebug(result) {
  const alphaA = findAlpha(state.correlation.alphaAId);
  const alphaB = findAlpha(state.correlation.alphaBId);
  nodes.correlationDebugGrid.innerHTML = "";
  const chips = [
    `因子 A：${alphaA?.title || "未选择"}`,
    `因子 B：${alphaB?.title || "未选择"}`,
    `重叠区间：${result.debug.overlapDateRange.start} -> ${result.debug.overlapDateRange.end}`,
    `A 字段：${result.debug.factorA.usedRawFields.join(", ")}`,
    `B 字段：${result.debug.factorB.usedRawFields.join(", ")}`,
    `A 的 Decay：${result.debug.factorA.decay}`,
    `B 的 Decay：${result.debug.factorB.decay}`,
    `有效天数：${result.debug.validDays}/${result.debug.totalSignalDays}`,
    `平均样本数：${formatNumber(result.debug.averageSampleCount, 0)}`,
  ];
  chips.forEach((item) => {
    const chip = document.createElement("span");
    chip.className = "debug-chip";
    chip.textContent = item;
    nodes.correlationDebugGrid.appendChild(chip);
  });
}

function renderCorrelationDebugPlaceholder(message) {
  nodes.correlationDebugGrid.innerHTML = `<div class="placeholder-row">${escapeHtml(message)}</div>`;
}

function renderCorrelationResult(result) {
  renderCorrelationSummary(result);
  renderCorrelationYearlyStats(result.yearlyStats);
  renderCorrelationDebug(result);
  nodes.correlationCaption.textContent =
    `均值 ${formatNumber(result.summary.averageCorrelation, 4)} / 比率 ${formatNumber(result.summary.correlationRatio, 2)}`;
  drawLineChart(
    nodes.correlationChart,
    result.correlationCurve || [],
    "#476f8f",
    (value) => formatNumber(value, 4)
  );
}

function renderCorrelationPanel() {
  ensureCorrelationSelection();
  renderCorrelationSelector(nodes.correlationAlphaA, state.correlation.alphaAId);
  renderCorrelationSelector(nodes.correlationAlphaB, state.correlation.alphaBId);

  const ready = Boolean(state.correlation.alphaAId && state.correlation.alphaBId);
  const running = isCorrelationRunning();
  nodes.correlationAlphaA.disabled = running || state.alphas.length < 2;
  nodes.correlationAlphaB.disabled = running || state.alphas.length < 2;
  nodes.runCorrelationButton.disabled = running || state.alphas.length < 2;
  nodes.correlationStatusText.textContent =
    state.correlation.message || correlationStatusLabel(state.correlation.status);
  nodes.correlationProgressBar.style.width = `${clamp(state.correlation.progress, 0, 1) * 100}%`;

  if (!ready) {
    renderCorrelationSummaryPlaceholder("至少需要两个因子窗口才能计算相关性。");
    renderCorrelationYearlyStats([]);
    renderCorrelationDebugPlaceholder("请选择两个因子窗口。");
    nodes.correlationCaption.textContent = "";
    setEmptyChart(nodes.correlationChart, "相关性结果会显示在这里。");
    return;
  }

  if (state.correlation.result) {
    renderCorrelationResult(state.correlation.result);
    return;
  }

  if (state.correlation.status === "failed") {
    renderCorrelationSummaryPlaceholder(state.correlation.message || "本次相关性计算失败。");
    renderCorrelationYearlyStats([]);
    renderCorrelationDebugPlaceholder(state.correlation.message || "错误信息会显示在这里。");
    nodes.correlationCaption.textContent = "";
    setEmptyChart(nodes.correlationChart, "本次相关性计算没有生成曲线。");
    return;
  }

  renderCorrelationSummaryPlaceholder(state.correlation.message || "选择两个因子后开始计算。");
  renderCorrelationYearlyStats([]);
  renderCorrelationDebugPlaceholder("会显示因子来源、重叠区间和有效样本。");
  nodes.correlationCaption.textContent = "";
  setEmptyChart(nodes.correlationChart, "相关性结果会显示在这里。");
}

function buildCorrelationPayload() {
  const alphaA = findAlpha(state.correlation.alphaAId);
  const alphaB = findAlpha(state.correlation.alphaBId);
  if (!alphaA || !alphaB) {
    throw new Error("Please choose two factor windows first.");
  }
  if (!alphaA.expression.trim() || !alphaB.expression.trim()) {
    throw new Error("Both factor windows need valid expressions before correlation.");
  }
  return {
    factorA: currentPayload(alphaA),
    factorB: currentPayload(alphaB),
  };
}

async function pollCorrelationJob(jobId, runToken) {
  while (true) {
    if (state.correlation.runToken !== runToken) {
      return;
    }
    const job = await fetchJson(`/api/correlations/${jobId}`);
    if (state.correlation.runToken !== runToken) {
      return;
    }
    state.correlation.status = job.status;
    state.correlation.progress = Number(job.progress || 0);
    state.correlation.message = job.message || state.correlation.message;
    state.correlation.jobId = jobId;
    if (job.status === "succeeded") {
      state.correlation.result = job.result;
      state.correlation.error = null;
      state.correlation.progress = 1;
      renderCorrelationPanel();
      return;
    }
    if (job.status === "failed") {
      state.correlation.result = null;
      state.correlation.error = job.message || "相关性计算失败。";
      state.correlation.progress = 1;
      renderCorrelationPanel();
      return;
    }
    renderCorrelationPanel();
    await sleep(1000);
  }
}

async function runCorrelationAnalysis() {
  try {
    const payload = buildCorrelationPayload();
    state.correlation.status = "queued";
    state.correlation.progress = 0;
    state.correlation.message = "正在提交相关性计算...";
    state.correlation.result = null;
    state.correlation.error = null;
    state.correlation.jobId = null;
    state.correlation.runToken = generateAlphaId();
    const runToken = state.correlation.runToken;
    renderCorrelationPanel();

    const job = await fetchJson("/api/correlations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (state.correlation.runToken !== runToken) {
      return;
    }

    state.correlation.jobId = job.jobId;
    state.correlation.status = "queued";
    state.correlation.message = "已进入相关性计算队列。";
    renderCorrelationPanel();
    await pollCorrelationJob(job.jobId, runToken);
  } catch (error) {
    state.correlation.status = "failed";
    state.correlation.progress = 1;
    state.correlation.result = null;
    state.correlation.error = error.message || "提交相关性计算失败。";
    state.correlation.message = state.correlation.error;
    renderCorrelationPanel();
  }
}

function buildAlphaSummary(alpha) {
  if (!alpha.result?.summary) {
    return `
      <div class="alpha-metric">${alpha.startDate} -> ${alpha.endDate}</div>
      <div class="alpha-metric">Decay ${alpha.decay}</div>
    `;
  }
  return `
    <div class="alpha-metric">${alpha.startDate} -> ${alpha.endDate}</div>
    <div class="alpha-metric">Decay ${alpha.decay}</div>
    <div class="alpha-metric">总收益 ${formatPercent(alpha.result.summary.totalReturn)}</div>
    <div class="alpha-metric">夏普 ${formatNumber(alpha.result.summary.sharpe, 2)}</div>
    <div class="alpha-metric">Turnover ${formatPercent(alpha.result.summary.turnover)}</div>
    <div class="alpha-metric">Margin ${formatBps(alpha.result.summary.margin)}</div>
    <div class="alpha-metric">最大回撤 ${formatPercent(alpha.result.summary.maxDrawdown)}</div>
  `;
}

function renderAlphaTabs() {
  const batchActive = isBatchActive();
  const bootError = Boolean(state.meta?.bootError);
  nodes.windowCount.textContent = `${state.alphas.length} 个窗口`;
  nodes.alphaTabList.innerHTML = "";
  nodes.addAlphaButton.disabled = bootError;
  nodes.runAllButton.disabled = bootError || batchActive;

  state.alphas.forEach((alpha) => {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = `alpha-tab alpha-tab-${alpha.status} ${alpha.id === state.selectedAlphaId ? "active" : ""}`;
    tab.addEventListener("click", () => selectAlpha(alpha.id));
    tab.textContent = alpha.title;
    tab.title = `${alpha.title} | ${alphaStatusLabel(alpha.status)}`;
    nodes.alphaTabList.appendChild(tab);
  });
}

function renderAlphaEditor() {
  const batchActive = isBatchActive();
  const bootError = Boolean(state.meta?.bootError);
  const selectedAlpha = getSelectedAlpha();
  nodes.alphaEditorShell.innerHTML = "";

  if (!selectedAlpha) {
    const empty = document.createElement("div");
    empty.className = "alpha-editor-placeholder";
    empty.textContent = "请选择一个因子窗口，或先添加新的因子。";
    nodes.alphaEditorShell.appendChild(empty);
    return;
  }

  const card = document.createElement("article");
  card.className = "alpha-card alpha-editor-card";

  const header = document.createElement("div");
  header.className = "alpha-card-header";

  const heading = document.createElement("div");
  const title = document.createElement("h3");
  title.textContent = selectedAlpha.title;
  const subtitle = document.createElement("span");
  subtitle.className = `alpha-status status-${selectedAlpha.status}`;
  subtitle.textContent = alphaStatusLabel(selectedAlpha.status);
  heading.appendChild(title);
  heading.appendChild(subtitle);

  const actions = document.createElement("div");
  actions.className = "alpha-actions";

  const runButton = document.createElement("button");
  runButton.type = "button";
  runButton.className = "ghost-button";
  runButton.textContent = "仅运行此窗";
  runButton.disabled = bootError || batchActive;
  runButton.addEventListener("click", (event) => {
    event.stopPropagation();
    runAlphaSet([selectedAlpha.id]);
  });

  const removeButton = document.createElement("button");
  removeButton.type = "button";
  removeButton.className = "ghost-button danger-button";
  removeButton.textContent = "删除";
  removeButton.disabled = bootError || batchActive || state.alphas.length <= 1;
  removeButton.addEventListener("click", (event) => {
    event.stopPropagation();
    removeAlphaWindow(selectedAlpha.id);
  });

  actions.appendChild(runButton);
  actions.appendChild(removeButton);
  header.appendChild(heading);
  header.appendChild(actions);

  const textarea = document.createElement("textarea");
  textarea.className = "alpha-textarea";
  textarea.placeholder = "在这里输入因子表达式。空窗口不会加入本轮回测。";
  textarea.value = selectedAlpha.expression;
  textarea.disabled = selectedAlpha.status === "queued" || selectedAlpha.status === "running";
  textarea.addEventListener("input", (event) => {
    setAlphaExpression(selectedAlpha.id, event.target.value);
  });

  const controls = document.createElement("div");
  controls.className = "alpha-control-grid";

  const startDateWrap = document.createElement("label");
  startDateWrap.className = "alpha-field-control";
  startDateWrap.innerHTML = "<span>开始日期</span>";

  const startDateInput = document.createElement("input");
  startDateInput.type = "date";
  startDateInput.min = defaultStartDate();
  startDateInput.max = defaultEndDate();
  startDateInput.value = selectedAlpha.startDate;
  startDateInput.disabled =
    selectedAlpha.status === "queued" || selectedAlpha.status === "running";
  startDateInput.addEventListener("input", (event) => {
    selectedAlpha.startDate = event.target.value || selectedAlpha.startDate;
  });
  startDateInput.addEventListener("change", (event) => {
    event.target.value = setAlphaDate(selectedAlpha.id, "startDate", event.target.value);
  });
  startDateInput.addEventListener("blur", (event) => {
    event.target.value = setAlphaDate(selectedAlpha.id, "startDate", event.target.value);
  });
  startDateWrap.appendChild(startDateInput);
  controls.appendChild(startDateWrap);

  const endDateWrap = document.createElement("label");
  endDateWrap.className = "alpha-field-control";
  endDateWrap.innerHTML = "<span>结束日期</span>";

  const endDateInput = document.createElement("input");
  endDateInput.type = "date";
  endDateInput.min = defaultStartDate();
  endDateInput.max = defaultEndDate();
  endDateInput.value = selectedAlpha.endDate;
  endDateInput.disabled =
    selectedAlpha.status === "queued" || selectedAlpha.status === "running";
  endDateInput.addEventListener("input", (event) => {
    selectedAlpha.endDate = event.target.value || selectedAlpha.endDate;
  });
  endDateInput.addEventListener("change", (event) => {
    event.target.value = setAlphaDate(selectedAlpha.id, "endDate", event.target.value);
  });
  endDateInput.addEventListener("blur", (event) => {
    event.target.value = setAlphaDate(selectedAlpha.id, "endDate", event.target.value);
  });
  endDateWrap.appendChild(endDateInput);
  controls.appendChild(endDateWrap);

  const decayWrap = document.createElement("label");
  decayWrap.className = "alpha-field-control alpha-decay-control";
  decayWrap.innerHTML = "<span>Decay</span>";

  const decayInput = document.createElement("input");
  decayInput.type = "number";
  decayInput.min = "1";
  decayInput.step = "1";
  decayInput.value = String(selectedAlpha.decay);
  decayInput.disabled =
    selectedAlpha.status === "queued" || selectedAlpha.status === "running";
  decayInput.addEventListener("input", (event) => {
    const nextValue = Number(event.target.value);
    selectedAlpha.decay =
      Number.isFinite(nextValue) && nextValue >= 1 ? nextValue : selectedAlpha.decay;
  });
  decayInput.addEventListener("change", (event) => {
    event.target.value = String(setAlphaDecay(selectedAlpha.id, event.target.value));
  });
  decayInput.addEventListener("blur", (event) => {
    event.target.value = String(setAlphaDecay(selectedAlpha.id, event.target.value));
  });
  decayWrap.appendChild(decayInput);
  controls.appendChild(decayWrap);

  const footer = document.createElement("div");
  footer.className = "alpha-card-footer";

  const message = document.createElement("div");
  message.className = "alpha-message";
  message.textContent = selectedAlpha.message;

  const progressShell = document.createElement("div");
  progressShell.className = "mini-progress-shell";
  const progressBar = document.createElement("div");
  progressBar.className = "mini-progress-bar";
  progressBar.style.width = `${clamp(selectedAlpha.progress, 0, 1) * 100}%`;
  progressShell.appendChild(progressBar);

  const summary = document.createElement("div");
  summary.className = "alpha-summary-row";
  summary.innerHTML = buildAlphaSummary(selectedAlpha);

  footer.appendChild(message);
  footer.appendChild(progressShell);
  footer.appendChild(summary);

  card.appendChild(header);
  card.appendChild(textarea);
  card.appendChild(controls);
  card.appendChild(footer);
  nodes.alphaEditorShell.appendChild(card);
}

function renderAlphaWindows() {
  renderAlphaTabs();
  renderAlphaEditor();
  renderCorrelationPanel();
}

function renderAlphaUpdate(alphaId, options = {}) {
  renderAlphaTabs();
  if (options.forceEditor || state.selectedAlphaId === alphaId) {
    renderAlphaEditor();
    renderSelectedAlphaResult();
  }
}

function renderSummary(summary) {
  const items = [
    ["totalReturn", "总收益", "累计开盘到开盘收益"],
    ["annualizedReturn", "年化收益", "按 252 个交易日年化"],
    ["sharpe", "夏普", "基于日度实现收益计算"],
    ["turnover", "Turnover", "平均每个收益日的换手率"],
    ["margin", "Margin", "平均每日收益除以平均换手，单位 bps"],
    ["maxDrawdown", "最大回撤", "从峰值到谷值的回撤"],
    ["averageHoldings", "平均持仓数", "平均入选股票数量"],
    ["coverage", "平均覆盖率", "衰减后的有效分数覆盖率"],
    ["averageCandidateCount", "平均候选数", "筛选前可交易股票数量"],
  ];
  nodes.summaryGrid.innerHTML = "";
  items.forEach(([key, label, hint]) => {
    const card = document.createElement("div");
    card.className = "summary-card";
    card.innerHTML = `
      <span class="label">${label}</span>
      <span class="value">${formatSummaryValue(key, summary[key])}</span>
      <span class="hint">${hint}</span>
    `;
    nodes.summaryGrid.appendChild(card);
  });
}

function renderSummaryPlaceholder(message) {
  nodes.summaryGrid.innerHTML = `<div class="summary-placeholder">${escapeHtml(message)}</div>`;
}

function renderYearlyStats(rows) {
  if (!rows || !rows.length) {
    nodes.yearlyTableBody.innerHTML =
      '<tr><td colspan="6" class="placeholder-row">暂无年度统计。</td></tr>';
    return;
  }
  nodes.yearlyTableBody.innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(row.year)}</td>
          <td>${formatPercent(row.return)}</td>
          <td>${formatNumber(row.sharpe, 2)}</td>
          <td>${formatPercent(row.turnover)}</td>
          <td>${formatBps(row.margin)}</td>
          <td>${formatPercent(row.maxDrawdown)}</td>
        </tr>
      `
    )
    .join("");
}

function renderDebug(debug) {
  nodes.debugGrid.innerHTML = "";
  const chips = [
    `使用字段：${debug.usedRawFields.join(", ")}`,
    `信号区间：${debug.signalDateRange.start} -> ${debug.signalDateRange.end}`,
    `交易区间：${debug.tradeDateRange.start} -> ${debug.tradeDateRange.end}`,
    `收益天数：${debug.effectiveReturnDays}`,
    `平均覆盖率：${formatPercent(debug.averageSignalCoverage)}`,
    `平均持仓数：${debug.averageSelectedCount}`,
    `平均候选数：${debug.averageCandidateCount}`,
  ];
  chips.forEach((item) => {
    const chip = document.createElement("span");
    chip.className = "debug-chip";
    chip.textContent = item;
    nodes.debugGrid.appendChild(chip);
  });
}

function renderDebugPlaceholder(message) {
  nodes.debugGrid.innerHTML = `<div class="placeholder-row">${escapeHtml(message)}</div>`;
}

function setEmptyChart(container, text) {
  container.classList.add("empty-chart");
  container.textContent = text;
}

function drawLineChart(container, points, color, formatter) {
  if (!points || !points.length) {
    setEmptyChart(container, "暂无图表数据。");
    return;
  }

  container.classList.remove("empty-chart");
  const width = 720;
  const height = 260;
  const padding = 28;
  const values = points.map((item) => Number(item.value));
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    min -= 1;
    max += 1;
  }

  const xScale = (index) =>
    padding + (index / Math.max(points.length - 1, 1)) * (width - padding * 2);
  const yScale = (value) =>
    height - padding - ((value - min) / (max - min)) * (height - padding * 2);

  const polyline = points
    .map((point, index) => `${xScale(index)},${yScale(Number(point.value))}`)
    .join(" ");

  const baseline = yScale(values[0]);
  const labelMin = formatter(min);
  const labelMax = formatter(max);
  const labelLast = formatter(values[values.length - 1]);

  container.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" class="chart-svg" preserveAspectRatio="none">
      <defs>
        <linearGradient id="fill-${container.id}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${color}" stop-opacity="0.22"></stop>
          <stop offset="100%" stop-color="${color}" stop-opacity="0.02"></stop>
        </linearGradient>
      </defs>
      <rect x="0" y="0" width="${width}" height="${height}" fill="transparent"></rect>
      <line x1="${padding}" y1="${baseline}" x2="${width - padding}" y2="${baseline}" stroke="rgba(24, 35, 29, 0.10)" stroke-width="1"></line>
      <polyline
        points="${polyline} ${width - padding},${height - padding} ${padding},${height - padding}"
        fill="url(#fill-${container.id})"
        stroke="none"
      ></polyline>
      <polyline
        points="${polyline}"
        fill="none"
        stroke="${color}"
        stroke-width="3"
        stroke-linecap="round"
        stroke-linejoin="round"
      ></polyline>
      <text x="${padding}" y="18" fill="rgba(24,35,29,0.52)" font-size="12">${escapeHtml(labelMax)}</text>
      <text x="${padding}" y="${height - 10}" fill="rgba(24,35,29,0.52)" font-size="12">${escapeHtml(labelMin)}</text>
      <text x="${width - padding}" y="18" fill="${color}" font-size="12" text-anchor="end">${escapeHtml(labelLast)}</text>
    </svg>
  `;
}

function renderResult(result) {
  renderSummary(result.summary);
  renderYearlyStats(result.yearlyStats);
  renderDebug(result.debug);
  nodes.rangeBadge.textContent = `${result.debug.tradeDateRange.start} -> ${result.debug.tradeDateRange.end}`;
  nodes.equityCaption.textContent = `${result.equityCurve.length} 个点`;
  nodes.drawdownCaption.textContent = `最大回撤 ${formatPercent(result.summary.maxDrawdown)}`;
  drawLineChart(nodes.equityChart, result.equityCurve, "#1d6b57", (value) => formatNumber(value, 2));
  drawLineChart(nodes.drawdownChart, result.drawdownCurve, "#b6522d", (value) =>
    formatPercent(value)
  );
}

function renderSelectedAlphaResult() {
  const alpha = getSelectedAlpha();
  if (!alpha) {
    nodes.selectedAlphaName.textContent = "请选择因子窗口";
    nodes.selectedAlphaExpression.textContent = "当前还没有选中的因子表达式。";
    nodes.selectedAlphaStatus.textContent = "待运行";
    nodes.rangeBadge.textContent = "暂无结果";
    renderSummaryPlaceholder("请选择一个窗口，或先添加新的因子。");
    renderYearlyStats([]);
    renderDebugPlaceholder("结果调试信息会显示在这里。");
    nodes.equityCaption.textContent = "";
    nodes.drawdownCaption.textContent = "";
    setEmptyChart(nodes.equityChart, "运行一次回测后显示。");
    setEmptyChart(nodes.drawdownChart, "回撤结果会显示在这里。");
    return;
  }

  nodes.selectedAlphaName.textContent = alpha.title;
  nodes.selectedAlphaExpression.textContent = alpha.expression.trim()
    ? alpha.expression.trim()
    : "当前窗口还没有输入表达式。";
  nodes.selectedAlphaStatus.textContent = alphaStatusLabel(alpha.status);

  if (alpha.result) {
    renderResult(alpha.result);
    return;
  }

  if (alpha.status === "failed") {
    nodes.rangeBadge.textContent = "暂无结果";
    renderSummaryPlaceholder(alpha.message || "本次回测失败。");
    renderYearlyStats([]);
    renderDebugPlaceholder(alpha.message || "错误信息会显示在这里。");
    nodes.equityCaption.textContent = "";
    nodes.drawdownCaption.textContent = "";
    setEmptyChart(nodes.equityChart, "本次回测没有生成净值曲线。");
    setEmptyChart(nodes.drawdownChart, "本次回测没有生成回撤曲线。");
    return;
  }

  nodes.rangeBadge.textContent = "暂无结果";
  renderSummaryPlaceholder("当前因子尚未生成回测结果。");
  renderYearlyStats([]);
  renderDebugPlaceholder("回测完成后会显示字段使用、覆盖率和有效区间。");
  nodes.equityCaption.textContent = "";
  nodes.drawdownCaption.textContent = "";
  setEmptyChart(nodes.equityChart, "运行一次回测后显示。");
  setEmptyChart(nodes.drawdownChart, "回撤结果会显示在这里。");
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.message || "请求失败。");
  }
  return payload;
}

function collectRunnableAlphaIds(alphaIds) {
  const runnable = [];
  alphaIds.forEach((alphaId) => {
    const alpha = findAlpha(alphaId);
    if (!alpha) {
      return;
    }
    if (!alpha.expression.trim()) {
      setAlphaSkipped(alphaId, "表达式为空，本轮未运行。");
      return;
    }
    runnable.push(alphaId);
  });
  return runnable;
}

function currentPayload(alpha) {
  return {
    expression: alpha.expression.trim(),
    startDate: alpha.startDate,
    endDate: alpha.endDate,
    decay: sanitizeDecayValue(alpha.decay),
  };
}

async function pollJob(alphaId, jobId, runToken) {
  while (true) {
    const alpha = findAlpha(alphaId);
    if (!alpha || alpha.runToken !== runToken) {
      return;
    }

    const job = await fetchJson(`/api/backtests/${jobId}`);
    if (!alpha || alpha.runToken !== runToken) {
      return;
    }

    alpha.status = job.status;
    alpha.progress = Number(job.progress || 0);
    alpha.message = job.message || alpha.message;
    alpha.jobId = jobId;
    if (job.status === "succeeded") {
      alpha.result = job.result;
      alpha.error = null;
      alpha.progress = 1;
      renderAlphaUpdate(alphaId);
      return;
    }
    if (job.status === "failed") {
      alpha.result = null;
      alpha.error = job.message || "回测失败。";
      alpha.progress = 1;
      renderAlphaUpdate(alphaId);
      return;
    }

    renderAlphaUpdate(alphaId);
    await sleep(1000);
  }
}

async function runSingleAlphaJob(alphaId) {
  const alpha = prepareAlphaForRun(alphaId);
  if (!alpha) {
    return;
  }
  const runToken = alpha.runToken;
  renderAlphaUpdate(alphaId, { forceEditor: true });

  try {
    alpha.message = "正在提交任务...";
    renderAlphaUpdate(alphaId, { forceEditor: true });

    const job = await fetchJson("/api/backtests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentPayload(alpha)),
    });

    if (alpha.runToken !== runToken) {
      return;
    }

    alpha.jobId = job.jobId;
    alpha.status = "queued";
    alpha.message = "已进入本地回测队列。";
    renderAlphaUpdate(alphaId, { forceEditor: true });
    await pollJob(alphaId, job.jobId, runToken);
  } catch (error) {
    if (alpha.runToken !== runToken) {
      return;
    }
    alpha.status = "failed";
    alpha.progress = 1;
    alpha.result = null;
    alpha.error = error.message || "提交回测失败。";
    alpha.message = alpha.error;
    renderAlphaUpdate(alphaId);
  }
}

function batchSnapshot() {
  if (!state.batch) {
    return null;
  }
  const scoped = state.batch.alphaIds.map(findAlpha).filter(Boolean);
  const total = scoped.length;
  const queued = scoped.filter((item) => item.status === "queued").length;
  const running = scoped.filter((item) => item.status === "running").length;
  const succeeded = scoped.filter((item) => item.status === "succeeded").length;
  const failed = scoped.filter((item) => item.status === "failed").length;
  const completed = succeeded + failed;
  return { total, queued, running, succeeded, failed, completed };
}

function renderBatchStatus() {
  if (!state.batch) {
    updateBatchCard("等待运行。", 0);
    return;
  }

  const snapshot = batchSnapshot();
  if (!snapshot || snapshot.total === 0) {
    updateBatchCard("没有可运行的窗口。", 1);
    return;
  }

  if (snapshot.completed >= snapshot.total) {
    updateBatchCard(
      `本轮完成：成功 ${snapshot.succeeded} 个，失败 ${snapshot.failed} 个。`,
      1
    );
    return;
  }

  const progress = snapshot.completed / snapshot.total;
  updateBatchCard(
    `本轮进行中：运行中 ${snapshot.running} 个，排队 ${snapshot.queued} 个，已完成 ${snapshot.completed}/${snapshot.total}。`,
    progress
  );
}

async function runAlphaSet(alphaIds) {
  if (hasLiveJobs()) {
    return;
  }

  const runnableIds = collectRunnableAlphaIds(alphaIds);
  if (!runnableIds.length) {
    state.batch = null;
    renderBatchStatus();
    renderAlphaWindows();
    renderSelectedAlphaResult();
    return;
  }

  const requestedLimit =
    runnableIds.length === 1 ? 1 : configuredParallelLimit();
  const hardLimit = state.meta?.parallelLimitMax || requestedLimit;
  const limit = clamp(requestedLimit, 1, hardLimit);

  state.batch = {
    alphaIds: runnableIds.slice(),
    limit,
  };
  renderBatchStatus();
  renderAlphaWindows();
  renderSelectedAlphaResult();

  const queue = runnableIds.slice();
  let activeCount = 0;

  await new Promise((resolve) => {
    const launchNext = () => {
      while (activeCount < limit && queue.length) {
        const alphaId = queue.shift();
        activeCount += 1;
        runSingleAlphaJob(alphaId)
          .catch(() => undefined)
          .finally(() => {
            activeCount -= 1;
            renderBatchStatus();
            renderAlphaWindows();
            renderSelectedAlphaResult();
            if (!queue.length && activeCount === 0) {
              resolve();
              return;
            }
            launchNext();
          });
      }
    };
    launchNext();
  });

  renderBatchStatus();
  renderAlphaWindows();
  renderSelectedAlphaResult();
}

function runAllBacktests() {
  runAlphaSet(state.alphas.map((item) => item.id));
}

function buildAlphaSummary(alpha) {
  if (!alpha.result?.summary) {
    return `
      <div class="alpha-metric">${alpha.startDate} -> ${alpha.endDate}</div>
      <div class="alpha-metric">Decay ${alpha.decay}</div>
    `;
  }
  return `
    <div class="alpha-metric">${alpha.startDate} -> ${alpha.endDate}</div>
    <div class="alpha-metric">Decay ${alpha.decay}</div>
    <div class="alpha-metric">总收益 ${formatPercent(alpha.result.summary.totalReturn)}</div>
    <div class="alpha-metric">夏普 ${formatNumber(alpha.result.summary.sharpe, 2)}</div>
    <div class="alpha-metric">Turnover ${formatPercent(alpha.result.summary.turnover)}</div>
    <div class="alpha-metric">Margin ${formatBps(alpha.result.summary.margin)}</div>
    <div class="alpha-metric">IC ${formatNumber(alpha.result.summary.ic, 4)}</div>
    <div class="alpha-metric">ICIR ${formatNumber(alpha.result.summary.icir, 2)}</div>
    <div class="alpha-metric">最大回撤 ${formatPercent(alpha.result.summary.maxDrawdown)}</div>
  `;
}

function renderSummary(summary) {
  const items = [
    ["totalReturn", "总收益", "累计开盘到开盘收益"],
    ["annualizedReturn", "年化收益", "按 252 个交易日年化"],
    ["sharpe", "夏普", "基于日度实现收益计算"],
    ["turnover", "Turnover", "平均每个收益日的换手率"],
    ["margin", "Margin", "平均每日收益除以平均换手，单位 bps"],
    ["ic", "IC", "横截面因子分数与未来 5 日标签的平均相关系数"],
    ["icir", "ICIR", "平均 IC 除以 IC 的标准差"],
    ["maxDrawdown", "最大回撤", "从峰值到谷值的回撤"],
    ["averageHoldings", "平均持仓数", "平均入选股票数量"],
    ["coverage", "平均覆盖率", "衰减后的有效分数覆盖率"],
    ["averageCandidateCount", "平均候选数", "筛选前可交易股票数量"],
  ];
  nodes.summaryGrid.innerHTML = "";
  items.forEach(([key, label, hint]) => {
    const card = document.createElement("div");
    card.className = "summary-card";
    card.innerHTML = `
      <span class="label">${label}</span>
      <span class="value">${formatSummaryValue(key, summary[key])}</span>
      <span class="hint">${hint}</span>
    `;
    nodes.summaryGrid.appendChild(card);
  });
}

function renderYearlyStats(rows) {
  if (!rows || !rows.length) {
    nodes.yearlyTableBody.innerHTML =
      '<tr><td colspan="8" class="placeholder-row">暂无年度统计。</td></tr>';
    return;
  }
  nodes.yearlyTableBody.innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(row.year)}</td>
          <td>${formatPercent(row.return)}</td>
          <td>${formatNumber(row.sharpe, 2)}</td>
          <td>${formatPercent(row.turnover)}</td>
          <td>${formatBps(row.margin)}</td>
          <td>${formatNumber(row.ic, 4)}</td>
          <td>${formatNumber(row.icir, 2)}</td>
          <td>${formatPercent(row.maxDrawdown)}</td>
        </tr>
      `
    )
    .join("");
}

function renderDebug(debug) {
  nodes.debugGrid.innerHTML = "";
  const chips = [
    `使用字段：${debug.usedRawFields.join(", ")}`,
    `信号区间：${debug.signalDateRange.start} -> ${debug.signalDateRange.end}`,
    `交易区间：${debug.tradeDateRange.start} -> ${debug.tradeDateRange.end}`,
    `收益天数：${debug.effectiveReturnDays}`,
    `平均覆盖率：${formatPercent(debug.averageSignalCoverage)}`,
    `平均 IC：${formatNumber(debug.averageIC, 4)}`,
    `ICIR：${formatNumber(debug.icir, 2)}`,
    `平均持仓数：${debug.averageSelectedCount}`,
    `平均候选数：${debug.averageCandidateCount}`,
  ];
  chips.forEach((item) => {
    const chip = document.createElement("span");
    chip.className = "debug-chip";
    chip.textContent = item;
    nodes.debugGrid.appendChild(chip);
  });
}

function renderResult(result) {
  renderSummary(result.summary);
  renderYearlyStats(result.yearlyStats);
  renderDebug(result.debug);
  nodes.rangeBadge.textContent = `${result.debug.tradeDateRange.start} -> ${result.debug.tradeDateRange.end}`;
  nodes.equityCaption.textContent = `${result.equityCurve.length} 个点`;
  nodes.drawdownCaption.textContent = `最大回撤 ${formatPercent(result.summary.maxDrawdown)}`;
  nodes.icCaption.textContent = `均值 ${formatNumber(result.summary.ic, 4)} / ICIR ${formatNumber(result.summary.icir, 2)}`;
  drawLineChart(nodes.equityChart, result.equityCurve, "#1d6b57", (value) => formatNumber(value, 2));
  drawLineChart(nodes.drawdownChart, result.drawdownCurve, "#b6522d", (value) =>
    formatPercent(value)
  );
  drawLineChart(nodes.icChart, result.icCurve || [], "#2e6f95", (value) => formatNumber(value, 4));
}

function renderSelectedAlphaResult() {
  const alpha = getSelectedAlpha();
  if (!alpha) {
    nodes.selectedAlphaName.textContent = "请选择因子窗口";
    nodes.selectedAlphaExpression.textContent = "当前还没有选中的因子表达式。";
    nodes.selectedAlphaStatus.textContent = "待运行";
    nodes.rangeBadge.textContent = "暂无结果";
    renderSummaryPlaceholder("请选择一个窗口，或先添加新的因子。");
    renderYearlyStats([]);
    renderDebugPlaceholder("结果调试信息会显示在这里。");
    nodes.equityCaption.textContent = "";
    nodes.drawdownCaption.textContent = "";
    nodes.icCaption.textContent = "";
    setEmptyChart(nodes.equityChart, "运行一次回测后显示。");
    setEmptyChart(nodes.drawdownChart, "回撤结果会显示在这里。");
    setEmptyChart(nodes.icChart, "IC 结果会显示在这里。");
    return;
  }

  nodes.selectedAlphaName.textContent = alpha.title;
  nodes.selectedAlphaExpression.textContent = alpha.expression.trim()
    ? alpha.expression.trim()
    : "当前窗口还没有输入表达式。";
  nodes.selectedAlphaStatus.textContent = alphaStatusLabel(alpha.status);

  if (alpha.result) {
    renderResult(alpha.result);
    return;
  }

  if (alpha.status === "failed") {
    nodes.rangeBadge.textContent = "暂无结果";
    renderSummaryPlaceholder(alpha.message || "本次回测失败。");
    renderYearlyStats([]);
    renderDebugPlaceholder(alpha.message || "错误信息会显示在这里。");
    nodes.equityCaption.textContent = "";
    nodes.drawdownCaption.textContent = "";
    nodes.icCaption.textContent = "";
    setEmptyChart(nodes.equityChart, "本次回测没有生成净值曲线。");
    setEmptyChart(nodes.drawdownChart, "本次回测没有生成回撤曲线。");
    setEmptyChart(nodes.icChart, "本次回测没有生成 IC 曲线。");
    return;
  }

  nodes.rangeBadge.textContent = "暂无结果";
  renderSummaryPlaceholder("当前因子尚未生成回测结果。");
  renderYearlyStats([]);
  renderDebugPlaceholder("回测完成后会显示字段使用、覆盖率、IC 和有效区间。");
  nodes.equityCaption.textContent = "";
  nodes.drawdownCaption.textContent = "";
  nodes.icCaption.textContent = "";
  setEmptyChart(nodes.equityChart, "运行一次回测后显示。");
  setEmptyChart(nodes.drawdownChart, "回撤结果会显示在这里。");
  setEmptyChart(nodes.icChart, "IC 结果会显示在这里。");
}

function buildAlphaSummary(alpha) {
  if (!alpha.result?.summary) {
    return `
      <div class="alpha-metric">${alpha.startDate} -> ${alpha.endDate}</div>
      <div class="alpha-metric">Decay ${alpha.decay}</div>
    `;
  }
  return `
    <div class="alpha-metric">${alpha.startDate} -> ${alpha.endDate}</div>
    <div class="alpha-metric">Decay ${alpha.decay}</div>
    <div class="alpha-metric">总收益/Total Return ${formatPercent(alpha.result.summary.totalReturn)}</div>
    <div class="alpha-metric">夏普/Sharpe ${formatNumber(alpha.result.summary.sharpe, 2)}</div>
    <div class="alpha-metric">换手率/Turnover ${formatPercent(alpha.result.summary.turnover)}</div>
    <div class="alpha-metric">收益换手比/Margin ${formatBps(alpha.result.summary.margin)}</div>
    <div class="alpha-metric">信息系数/IC ${formatNumber(alpha.result.summary.ic, 4)}</div>
    <div class="alpha-metric">信息比率/ICIR ${formatNumber(alpha.result.summary.icir, 2)}</div>
    <div class="alpha-metric">最大回撤/Max Drawdown ${formatPercent(alpha.result.summary.maxDrawdown)}</div>
  `;
}

function renderSummary(summary) {
  const items = [
    ["totalReturn", "总收益/Total Return", "累计开盘到开盘收益"],
    ["annualizedReturn", "年化收益/Annualized Return", "按 252 个交易日年化"],
    ["sharpe", "夏普/Sharpe", "基于日度实现收益计算"],
    ["turnover", "换手率/Turnover", "平均每个收益日的换手率"],
    ["margin", "收益换手比/Margin", "平均每日收益除以平均换手，单位 bps"],
    ["ic", "信息系数/IC", "横截面因子分数与未来 5 日标签的平均相关系数"],
    ["icir", "信息比率/ICIR", "平均 IC 除以 IC 的标准差"],
    ["maxDrawdown", "最大回撤/Max Drawdown", "从峰值到谷值的回撤"],
    ["averageHoldings", "平均持仓数/Average Holdings", "平均入选股票数量"],
    ["coverage", "平均覆盖率/Coverage", "衰减后的有效分数覆盖率"],
    ["averageCandidateCount", "平均候选数/Average Candidates", "筛选前可交易股票数量"],
  ];
  nodes.summaryGrid.innerHTML = "";
  items.forEach(([key, label, hint]) => {
    const card = document.createElement("div");
    card.className = "summary-card";
    card.innerHTML = `
      <span class="label">${label}</span>
      <span class="value">${formatSummaryValue(key, summary[key])}</span>
      <span class="hint">${hint}</span>
    `;
    nodes.summaryGrid.appendChild(card);
  });
}

async function bootstrap() {
  try {
    const [meta, catalog] = await Promise.all([
      fetchJson("/api/meta"),
      fetchJson("/api/catalog"),
    ]);
    state.meta = meta;
    state.catalog = catalog;

    renderFixedRules();
    renderExamples();
    renderRulesHelp();
    renderCatalog();

    createAlphaWindow(meta.defaultExpression || window.APP_DEFAULTS.defaultExpression, {
      select: true,
      startDate: window.APP_DEFAULTS.startDate || meta.dateRange?.start,
      endDate: window.APP_DEFAULTS.endDate || meta.dateRange?.end,
    });

    if (meta.bootError) {
      updateBatchCard(meta.bootError, 1);
      renderAlphaWindows();
      renderSelectedAlphaResult();
      return;
    }

    renderBatchStatus();
  } catch (error) {
    updateBatchCard(error.message || "页面初始化失败。", 1);
    renderSummaryPlaceholder("页面初始化失败。");
    renderDebugPlaceholder("请检查后端服务是否正常启动。");
  }
}

nodes.addAlphaButton.addEventListener("click", () => {
  createAlphaWindow("");
});

nodes.runAllButton.addEventListener("click", runAllBacktests);
nodes.runCorrelationButton.addEventListener("click", runCorrelationAnalysis);

nodes.correlationAlphaA.addEventListener("change", (event) => {
  setCorrelationSelection("A", event.target.value);
  renderCorrelationPanel();
});

nodes.correlationAlphaB.addEventListener("change", (event) => {
  setCorrelationSelection("B", event.target.value);
  renderCorrelationPanel();
});

nodes.catalogSearch.addEventListener("input", (event) => {
  state.search = event.target.value || "";
  renderCatalog();
});

nodes.filterButtons.forEach((button) => {
  button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    nodes.filterButtons.forEach((item) => item.classList.toggle("active", item === button));
    renderCatalog();
  });
});

bootstrap();
