const state = {
  meta: null,
  catalog: null,
  filter: "all",
  search: "",
  pollHandle: null,
};

const nodes = {
  expression: document.getElementById("expression"),
  startDate: document.getElementById("start-date"),
  endDate: document.getElementById("end-date"),
  decay: document.getElementById("decay"),
  runButton: document.getElementById("run-backtest"),
  statusText: document.getElementById("status-text"),
  progressBar: document.getElementById("progress-bar"),
  fixedRulesList: document.getElementById("fixed-rules-list"),
  exampleButtons: document.getElementById("example-buttons"),
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

function setStatus(message, progress = 0) {
  nodes.statusText.textContent = message;
  nodes.progressBar.style.width = `${Math.max(0, Math.min(100, progress * 100))}%`;
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

function renderFixedRules() {
  if (!state.meta) return;
  nodes.fixedRulesList.innerHTML = "";
  state.meta.fixedRules.forEach((item) => {
    const tag = document.createElement("span");
    tag.textContent = item;
    nodes.fixedRulesList.appendChild(tag);
  });
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
      nodes.expression.value = item.expression;
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
  if (!state.catalog || !state.catalog.backtestRules) return;
  nodes.rulesHelpList.innerHTML = "";
  state.catalog.backtestRules.forEach((section) => {
    const card = document.createElement("article");
    card.className = "rule-card";
    card.innerHTML = `
      <h3>${section.title}</h3>
      <ul>
        ${section.items.map((item) => `<li>${item}</li>`).join("")}
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
      noteBits.push(`<div><strong>维度</strong><span>${item.dimensions}</span></div>`);
    }
    if (item.definition) {
      noteBits.push(`<div><strong>定义</strong><code>${item.definition}</code></div>`);
    }
    if (item.returns) {
      noteBits.push(`<div><strong>返回</strong><span>${item.returns}</span></div>`);
    }
    if (item.notes && item.category !== "raw") {
      noteBits.push(`<div><strong>备注</strong><span>${item.notes}</span></div>`);
    }
    if (item.example) {
      noteBits.push(`<div><strong>示例</strong><code>${item.example}</code></div>`);
    }
    if (item.expressionReady && item.category === "raw") {
      noteBits.push(
        `<div><strong>表达式</strong><span>${item.expressionReady === "true" ? "可直接使用" : "仅查询展示"}</span></div>`
      );
    }

    card.innerHTML = `
      <div class="topline">
        <div>
          <h3>${item.displayName || item.name}</h3>
          <code>${title}</code>
        </div>
        <span class="tag">${categoryLabel(item.category)}</span>
      </div>
      <p>${description}</p>
      <div class="metadata-grid">
        ${noteBits.join("")}
      </div>
    `;
    nodes.catalogList.appendChild(card);
  });
}

function renderSummary(summary) {
  const items = [
    ["totalReturn", "总收益", "累计开盘到开盘收益"],
    ["annualizedReturn", "年化收益", "按252个交易日年化"],
    ["sharpe", "夏普", "基于日度实现收益计算"],
    ["maxDrawdown", "最大回撤", "从峰值到谷值的回撤"],
    ["averageTurnover", "平均换手", "平均每日换手率"],
    ["averageHoldings", "平均持仓数", "平均入选股票数量"],
    ["coverage", "平均覆盖率", "衰减后有效分数覆盖率"],
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
      '<tr><td colspan="4" class="placeholder-row">暂无年度统计。</td></tr>';
    return;
  }
  nodes.yearlyTableBody.innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${row.year}</td>
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
    `覆盖率：${formatPercent(debug.averageSignalCoverage)}`,
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

function drawLineChart(container, points, color, formatter) {
  if (!points || !points.length) {
    container.classList.add("empty-chart");
    container.textContent = "暂无图表数据。";
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
      <line x1="${padding}" y1="${baseline}" x2="${width - padding}" y2="${baseline}" stroke="rgba(24,35,29,0.10)" stroke-width="1"></line>
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
      <text x="${padding}" y="18" fill="rgba(24,35,29,0.52)" font-size="12">${labelMax}</text>
      <text x="${padding}" y="${height - 10}" fill="rgba(24,35,29,0.52)" font-size="12">${labelMin}</text>
      <text x="${width - padding}" y="18" fill="${color}" font-size="12" text-anchor="end">${labelLast}</text>
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
  drawLineChart(nodes.equityChart, result.equityCurve, "#1d6b57", (v) => formatNumber(v, 2));
  drawLineChart(nodes.drawdownChart, result.drawdownCurve, "#b6522d", (v) => formatPercent(v));
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.message || "请求失败。");
  }
  return payload;
}

async function submitBacktest() {
  const payload = {
    expression: nodes.expression.value.trim(),
    startDate: nodes.startDate.value,
    endDate: nodes.endDate.value,
    decay: Number(nodes.decay.value || 1),
  };

  nodes.runButton.disabled = true;
  setStatus("正在提交回测...", 0.05);
  try {
    const job = await fetchJson("/api/backtests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    pollJob(job.jobId);
  } catch (error) {
    nodes.runButton.disabled = false;
    setStatus(error.message || "提交回测失败。", 1);
  }
}

async function pollJob(jobId) {
  if (state.pollHandle) {
    clearInterval(state.pollHandle);
  }

  const tick = async () => {
    try {
      const job = await fetchJson(`/api/backtests/${jobId}`);
      setStatus(job.message || job.status, Number(job.progress || 0));
      if (job.status === "succeeded") {
        clearInterval(state.pollHandle);
        state.pollHandle = null;
        nodes.runButton.disabled = false;
        renderResult(job.result);
      } else if (job.status === "failed") {
        clearInterval(state.pollHandle);
        state.pollHandle = null;
        nodes.runButton.disabled = false;
      }
    } catch (error) {
      clearInterval(state.pollHandle);
      state.pollHandle = null;
      nodes.runButton.disabled = false;
      setStatus(error.message || "轮询任务失败。", 1);
    }
  };

  await tick();
  state.pollHandle = setInterval(tick, 1000);
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
    if (meta.dateRange && meta.dateRange.start && meta.dateRange.end) {
      nodes.startDate.min = meta.dateRange.start;
      nodes.startDate.max = meta.dateRange.end;
      nodes.endDate.min = meta.dateRange.start;
      nodes.endDate.max = meta.dateRange.end;
      nodes.startDate.value = window.APP_DEFAULTS.startDate;
      nodes.endDate.value = window.APP_DEFAULTS.endDate;
    }
    if (meta.bootError) {
      nodes.runButton.disabled = true;
      setStatus(meta.bootError, 1);
      return;
    }
    setStatus("就绪。", 0);
  } catch (error) {
    setStatus(error.message || "页面初始化失败。", 1);
  }
}

nodes.runButton.addEventListener("click", submitBacktest);
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
