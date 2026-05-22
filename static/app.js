const state = {
  meta: null,
  catalog: null,
  filter: "all",
  search: "",
  alphas: [],
  selectedAlphaId: null,
  nextAlphaNumber: 1,
  batch: null,
};

const nodes = {
  startDate: document.getElementById("start-date"),
  endDate: document.getElementById("end-date"),
  addAlphaButton: document.getElementById("add-alpha"),
  runAllButton: document.getElementById("run-all-backtests"),
  windowCount: document.getElementById("window-count"),
  alphaWindows: document.getElementById("alpha-windows"),
  batchStatusText: document.getElementById("batch-status-text"),
  batchProgressBar: document.getElementById("batch-progress-bar"),
  fixedRulesList: document.getElementById("fixed-rules-list"),
  exampleButtons: document.getElementById("example-buttons"),
  selectedAlphaName: document.getElementById("selected-alpha-name"),
  selectedAlphaExpression: document.getElementById("selected-alpha-expression"),
  selectedAlphaStatus: document.getElementById("selected-alpha-status"),
  summaryGrid: document.getElementById("summary-grid"),
  equityChart: document.getElementById("equity-chart"),
  drawdownChart: document.getElementById("drawdown-chart"),
  equityCaption: document.getElementById("equity-caption"),
  drawdownCaption: document.getElementById("drawdown-caption"),
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

function formatSummaryValue(key, value) {
  const percentKeys = new Set([
    "totalReturn",
    "annualizedReturn",
    "maxDrawdown",
    "averageTurnover",
    "coverage",
  ]);
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

function createAlphaWindow(expression = "", options = {}) {
  const alpha = {
    id: generateAlphaId(),
    title: `因子 ${state.nextAlphaNumber}`,
    expression,
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
  if (state.selectedAlphaId === alphaId) {
    nodes.selectedAlphaExpression.textContent = expression.trim()
      ? expression.trim()
      : "当前窗口还没有输入表达式。";
  }
}

function setAlphaDecay(alphaId, decay) {
  const alpha = findAlpha(alphaId);
  if (!alpha) {
    return;
  }
  alpha.decay = sanitizeDecayValue(decay);
  renderAlphaWindows();
  renderSelectedAlphaResult();
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
      if (!alpha) {
        createAlphaWindow(item.expression);
        return;
      }
      alpha.expression = item.expression;
      renderAlphaWindows();
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

function buildAlphaSummary(alpha) {
  if (!alpha.result?.summary) {
    return `<div class="alpha-metric">Decay ${alpha.decay}</div>`;
  }
  return `
    <div class="alpha-metric">Decay ${alpha.decay}</div>
    <div class="alpha-metric">总收益 ${formatPercent(alpha.result.summary.totalReturn)}</div>
    <div class="alpha-metric">夏普 ${formatNumber(alpha.result.summary.sharpe, 2)}</div>
    <div class="alpha-metric">最大回撤 ${formatPercent(alpha.result.summary.maxDrawdown)}</div>
  `;
}

function renderAlphaWindows() {
  const batchActive = isBatchActive();
  const bootError = Boolean(state.meta?.bootError);
  nodes.windowCount.textContent = `${state.alphas.length} 个窗口`;
  nodes.alphaWindows.innerHTML = "";
  nodes.addAlphaButton.disabled = bootError || batchActive;
  nodes.runAllButton.disabled = bootError || batchActive;
  nodes.startDate.disabled = bootError || batchActive;
  nodes.endDate.disabled = bootError || batchActive;

  state.alphas.forEach((alpha) => {
    const card = document.createElement("article");
    card.className = `alpha-card ${alpha.id === state.selectedAlphaId ? "active" : ""}`;
    card.addEventListener("click", () => selectAlpha(alpha.id));

    const header = document.createElement("div");
    header.className = "alpha-card-header";

    const heading = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = alpha.title;
    const subtitle = document.createElement("span");
    subtitle.className = `alpha-status status-${alpha.status}`;
    subtitle.textContent = alphaStatusLabel(alpha.status);
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
      runAlphaSet([alpha.id]);
    });

    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "ghost-button danger-button";
    removeButton.textContent = "删除";
    removeButton.disabled = bootError || batchActive || state.alphas.length <= 1;
    removeButton.addEventListener("click", (event) => {
      event.stopPropagation();
      removeAlphaWindow(alpha.id);
    });

    actions.appendChild(runButton);
    actions.appendChild(removeButton);
    header.appendChild(heading);
    header.appendChild(actions);

    const textarea = document.createElement("textarea");
    textarea.className = "alpha-textarea";
    textarea.placeholder = "在这里输入因子表达式。空窗口不会加入本轮回测。";
    textarea.value = alpha.expression;
    textarea.disabled = batchActive || alpha.status === "queued" || alpha.status === "running";
    textarea.addEventListener("input", (event) => {
      setAlphaExpression(alpha.id, event.target.value);
    });
    textarea.addEventListener("click", (event) => {
      event.stopPropagation();
    });

    const controls = document.createElement("div");
    controls.className = "alpha-control-row";

    const decayWrap = document.createElement("label");
    decayWrap.className = "alpha-decay-control";
    decayWrap.innerHTML = '<span>Decay</span>';

    const decayInput = document.createElement("input");
    decayInput.type = "number";
    decayInput.min = "1";
    decayInput.step = "1";
    decayInput.value = String(alpha.decay);
    decayInput.disabled = batchActive || alpha.status === "queued" || alpha.status === "running";
    decayInput.addEventListener("click", (event) => {
      event.stopPropagation();
    });
    decayInput.addEventListener("input", (event) => {
      const nextValue = Number(event.target.value);
      alpha.decay = Number.isFinite(nextValue) && nextValue >= 1 ? nextValue : alpha.decay;
    });
    decayInput.addEventListener("change", (event) => {
      event.stopPropagation();
      setAlphaDecay(alpha.id, event.target.value);
    });
    decayInput.addEventListener("blur", (event) => {
      setAlphaDecay(alpha.id, event.target.value);
    });
    decayWrap.appendChild(decayInput);
    controls.appendChild(decayWrap);

    const footer = document.createElement("div");
    footer.className = "alpha-card-footer";

    const message = document.createElement("div");
    message.className = "alpha-message";
    message.textContent = alpha.message;

    const progressShell = document.createElement("div");
    progressShell.className = "mini-progress-shell";
    const progressBar = document.createElement("div");
    progressBar.className = "mini-progress-bar";
    progressBar.style.width = `${clamp(alpha.progress, 0, 1) * 100}%`;
    progressShell.appendChild(progressBar);

    const summary = document.createElement("div");
    summary.className = "alpha-summary-row";
    summary.innerHTML = buildAlphaSummary(alpha);

    footer.appendChild(message);
    footer.appendChild(progressShell);
    footer.appendChild(summary);

    card.appendChild(header);
    card.appendChild(textarea);
    card.appendChild(controls);
    card.appendChild(footer);
    nodes.alphaWindows.appendChild(card);
  });
}

function renderSummary(summary) {
  const items = [
    ["totalReturn", "总收益", "累计开盘到开盘收益"],
    ["annualizedReturn", "年化收益", "按 252 个交易日年化"],
    ["sharpe", "夏普", "基于日度实现收益计算"],
    ["maxDrawdown", "最大回撤", "从峰值到谷值的回撤"],
    ["averageTurnover", "平均换手", "平均每个收益日的换手率"],
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
      '<tr><td colspan="4" class="placeholder-row">暂无年度统计。</td></tr>';
    return;
  }
  nodes.yearlyTableBody.innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(row.year)}</td>
          <td>${formatPercent(row.return)}</td>
          <td>${formatNumber(row.sharpe, 2)}</td>
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
    nodes.rangeBadge.textContent = "运行失败";
    renderSummaryPlaceholder(alpha.message || "本次回测失败。");
    renderYearlyStats([]);
    renderDebugPlaceholder(alpha.message || "错误信息会显示在这里。");
    nodes.equityCaption.textContent = "";
    nodes.drawdownCaption.textContent = "";
    setEmptyChart(nodes.equityChart, "本次回测没有生成净值曲线。");
    setEmptyChart(nodes.drawdownChart, "本次回测没有生成回撤曲线。");
    return;
  }

  nodes.rangeBadge.textContent = alpha.status === "running" || alpha.status === "queued"
    ? alpha.message
    : "暂无结果";
  renderSummaryPlaceholder(alpha.message || "运行一次回测后显示。");
  renderYearlyStats([]);
  renderDebugPlaceholder(alpha.message || "结果调试信息会显示在这里。");
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

function currentPayload(alpha, settings) {
  return {
    expression: alpha.expression.trim(),
    startDate: settings.startDate,
    endDate: settings.endDate,
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
      renderAlphaWindows();
      renderSelectedAlphaResult();
      return;
    }
    if (job.status === "failed") {
      alpha.result = null;
      alpha.error = job.message || "回测失败。";
      alpha.progress = 1;
      renderAlphaWindows();
      renderSelectedAlphaResult();
      return;
    }

    renderAlphaWindows();
    renderSelectedAlphaResult();
    await sleep(1000);
  }
}

async function runSingleAlphaJob(alphaId, settings) {
  const alpha = prepareAlphaForRun(alphaId);
  if (!alpha) {
    return;
  }
  const runToken = alpha.runToken;
  renderAlphaWindows();
  renderSelectedAlphaResult();

  try {
    alpha.message = "正在提交任务...";
    renderAlphaWindows();
    renderSelectedAlphaResult();

    const job = await fetchJson("/api/backtests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentPayload(alpha, settings)),
    });

    if (alpha.runToken !== runToken) {
      return;
    }

    alpha.jobId = job.jobId;
    alpha.status = "queued";
    alpha.message = "已进入本地回测队列。";
    renderAlphaWindows();
    renderSelectedAlphaResult();
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
    renderAlphaWindows();
    renderSelectedAlphaResult();
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
  const settings = {
    startDate: nodes.startDate.value,
    endDate: nodes.endDate.value,
  };

  state.batch = {
    alphaIds: runnableIds.slice(),
    limit,
    settings,
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
        runSingleAlphaJob(alphaId, settings)
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

    if (meta.dateRange?.start && meta.dateRange?.end) {
      nodes.startDate.min = meta.dateRange.start;
      nodes.startDate.max = meta.dateRange.end;
      nodes.endDate.min = meta.dateRange.start;
      nodes.endDate.max = meta.dateRange.end;
      nodes.startDate.value = window.APP_DEFAULTS.startDate;
      nodes.endDate.value = window.APP_DEFAULTS.endDate;
    }

    createAlphaWindow(meta.defaultExpression || window.APP_DEFAULTS.defaultExpression, {
      select: true,
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
