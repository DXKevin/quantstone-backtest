type RunStatus = "running" | "completed" | "failed" | "cancelled" | "interrupted" | string;
type StrategyScope = "all" | "running" | "archived";
type DetailTab = "positions" | "orders" | "trades" | "logs";
type WorkTab = "code" | "results" | "history";
type Row = Record<string, any>;

interface RunSummary extends Row {
  id: string; name: string; status: RunStatus; strategy_id?: string | null;
  market?: string; source: string; frequency: string; start: string; end: string; created_at: string;
}
interface Strategy extends Row {
  id: string; name: string; description: string; instrument_type: string; market: string;
  frequency: string; folder: string; tags: string[]; code: string; status: string;
  updated_at: string; run_count: number; last_run?: RunSummary;
}
interface BacktestResult extends Row {
  metrics: {total_return: string; max_drawdown: string; trade_count: number};
  account: Row & {initial_cash: string; available_cash: string; market_value: string; equity: string; realized_pnl: string; unrealized_pnl: string; total_pnl: string; total_fees: string; net_value: string; positions: Row[]};
  equity_curve: Array<{date: string; net_value: string}>;
  orders: Row[]; trades: Row[]; logs: Array<{time: string; message: string}>; data?: {note?: string};
}
interface Job extends RunSummary {
  progress?: {stage?: string; completed?: number; total?: number; date?: string};
  result?: BacktestResult; console?: string; error?: string;
}
interface ApiErrorDetail {loc: Array<string | number>; msg: string}
interface ApiErrorResponse {detail?: string | ApiErrorDetail[]}
type TableColumn<T extends Row> = [string, (row: T) => string | number | Node | null | undefined];

declare global {
  interface Window {
    quantstoneEditor?: {getValue(): string; setValue(value: string): void; focus(): void};
  }
}

const $ = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const element = document.getElementById(id);
  if (!element) throw new Error(`页面缺少元素 #${id}`);
  return element as T;
};
const formValue = (id: string) => $<HTMLInputElement | HTMLSelectElement>(id).value;
const states: Record<string, string> = {running: "运行中", completed: "已完成", failed: "失败", cancelled: "已取消", interrupted: "已中断"};
const fields = ["name", "frequency", "minute_label", "start", "end", "initial_cash"] as const;
let strategies: Strategy[] = [], currentStrategy: Strategy | null = null, selected: string | null = null, job: Job | null = null;
let timer: ReturnType<typeof setTimeout> | undefined, activeTab: DetailTab = "positions", exampleCode = "", dirty = false;
let scope: StrategyScope = "all", collapsedFolders = new Set<string>();
try { collapsedFolders = new Set<string>(JSON.parse(localStorage.getItem("quantstone.strategyFolders.collapsed") || "[]")); } catch {}
const isStrategyPage = document.body.classList.contains("strategy-page");
const getStrategyCode = () => window.quantstoneEditor?.getValue() ?? $<HTMLTextAreaElement>("strategy").value;
const setStrategyCode = (value: string) => window.quantstoneEditor ? window.quantstoneEditor.setValue(value) : $<HTMLTextAreaElement>("strategy").value = value;
const errorMessage = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);

function showError(message: string) {
  $("notice").textContent = message; $("notice").hidden = !message;
  if (message) scrollTo({top: 0, behavior: "smooth"});
}
async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {...options, headers: {"Content-Type": "application/json", "X-Quantstone-Client": "workbench", ...options.headers}});
  const result = await response.json() as T & ApiErrorResponse;
  if (!response.ok) {
    let detail = result.detail;
    if (Array.isArray(detail)) detail = detail.map(item => `${item.loc.join(".")}: ${item.msg}`).join("\n");
    throw new Error(detail || "请求失败");
  }
  return result;
}
const fmt = (value: string | number, places = 2) => Number(value).toLocaleString("zh-CN", {minimumFractionDigits: places, maximumFractionDigits: places});
const shortDate = (value?: string) => value ? new Date(value).toLocaleString("zh-CN", {year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"}) : "—";
function node<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text?: string): HTMLElementTagNameMap[K] {
  const element = document.createElement(tag); if (className) element.className = className;
  if (text !== undefined) element.textContent = text; return element;
}
function folderIcon() {
  const namespace = "http://www.w3.org/2000/svg", svg = document.createElementNS(namespace, "svg");
  svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("aria-hidden", "true"); svg.classList.add("folder-icon");
  const path = document.createElementNS(namespace, "path");
  path.setAttribute("d", "M3.5 6.5c0-.83.67-1.5 1.5-1.5h4.2l2 2H19c.83 0 1.5.67 1.5 1.5v9c0 .83-.67 1.5-1.5 1.5H5c-.83 0-1.5-.67-1.5-1.5z");
  svg.append(path); return svg;
}
function pencilIcon() {
  const namespace = "http://www.w3.org/2000/svg", svg = document.createElementNS(namespace, "svg");
  svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(namespace, "path");
  path.setAttribute("d", "M15.7 5.3a2.4 2.4 0 0 1 3.4 3.4L9 18.8 4.5 19.5l.7-4.5zM14.3 6.7l3 3");
  svg.append(path); return svg;
}
function strategyStatus(lastRun?: RunSummary) {
  if (!lastRun) return {label: "尚未回测", className: ""};
  return {label: states[lastRun.status] || lastRun.status, className: lastRun.status};
}

function renderStrategyList() {
  const query = formValue("strategy-search").trim().toLowerCase(), market = formValue("market-filter"), frequency = formValue("frequency-filter"), status = formValue("status-filter"), instrument = formValue("instrument-filter");
  const filtered = strategies.filter(item => {
    const haystack = [item.name, item.description, ...item.tags].join(" ").toLowerCase();
    const inScope = scope === "all" || (scope === "running" ? item.last_run?.status === "running" : item.status === "archived");
    return inScope && (!query || haystack.includes(query)) && (!instrument || item.instrument_type === instrument) && (!market || item.market === market) && (!frequency || item.frequency === frequency) && (!status || item.status === status);
  });
  const body = $<HTMLTableSectionElement>("strategy-list"); body.replaceChildren();
  $("strategy-empty").hidden = filtered.length > 0; $("list-count").textContent = `显示 ${filtered.length} 个策略 / 共 ${strategies.length} 个`;
  const folders = new Map<string, Strategy[]>();
  for (const item of filtered) { const folder = item.folder || "默认目录"; if (!folders.has(folder)) folders.set(folder, []); folders.get(folder)!.push(item); }
  for (const [folder, items] of [...folders.entries()].sort((a, b) => a[0].localeCompare(b[0], "zh-CN"))) {
    const folderRow = node("tr", "folder-row"); folderRow.tabIndex = 0;
    const folderCell = node("td"); folderCell.colSpan = 6; const folderLine = node("div", "folder-line");
    const folderButton = node("button", "folder-toggle"); folderButton.type = "button";
    const collapsed = collapsedFolders.has(folder); folderButton.setAttribute("aria-expanded", String(!collapsed));
    folderButton.append(node("span", "folder-caret", collapsed ? "›" : "⌄"), folderIcon(), node("span", "folder-name", folder));
    const toggle = () => { collapsedFolders.has(folder) ? collapsedFolders.delete(folder) : collapsedFolders.add(folder); try { localStorage.setItem("quantstone.strategyFolders.collapsed", JSON.stringify([...collapsedFolders])); } catch {} renderStrategyList(); };
    folderButton.onclick = toggle; folderRow.onkeydown = event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); } };
    const renameButton = node("button", "folder-rename"); renameButton.type = "button"; renameButton.title = "重命名目录"; renameButton.setAttribute("aria-label", `重命名 ${folder}`); renameButton.append(pencilIcon());
    renameButton.onclick = event => {
      event.stopPropagation();
      const form = node("form", "folder-rename-form"), input = node("input", "folder-rename-input"), save = node("button", "folder-rename-save", "保存"), cancel = node("button", "folder-rename-cancel", "取消");
      input.value = folder; input.maxLength = 120; input.setAttribute("aria-label", "新目录名称"); save.type = "submit"; cancel.type = "button";
      const restore = () => renderStrategyList(); cancel.onclick = restore;
      input.onkeydown = keyEvent => { if (keyEvent.key === "Escape") { keyEvent.preventDefault(); restore(); } };
      form.onsubmit = async submitEvent => {
        submitEvent.preventDefault(); const replacement = input.value.trim();
        if (!replacement || replacement === folder) { restore(); return; }
        save.disabled = true;
        try {
          await api<{strategy_count: number}>("/api/strategy-folders/rename", {method: "POST", body: JSON.stringify({current: folder, replacement})});
          if (collapsedFolders.delete(folder)) collapsedFolders.add(replacement);
          try { localStorage.setItem("quantstone.strategyFolders.collapsed", JSON.stringify([...collapsedFolders])); } catch {}
          await loadStrategies();
        } catch (reason) { showError(errorMessage(reason)); save.disabled = false; input.focus(); }
      };
      form.append(folderIcon(), input, save, cancel); folderCell.replaceChildren(form); input.focus(); input.select();
    };
    folderLine.append(folderButton, renameButton, node("span", "folder-count", String(items.length))); folderCell.append(folderLine); folderRow.append(folderCell); body.append(folderRow); if (collapsed) continue;
    for (const item of items) {
      const row = node("tr", "strategy-row"); row.tabIndex = 0; row.setAttribute("aria-label", `进入策略：${item.name}`);
      row.onclick = () => location.assign(`/strategies/${item.id}`); row.onkeydown = event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); row.click(); } };
      const first = node("td"), wrap = node("div", "strategy-name"), content = node("div"), link = node("a");
      link.textContent = item.name; link.href = `/strategies/${item.id}`; link.onclick = event => event.stopPropagation();
      content.append(link, node("p", "", item.description || "暂无策略说明")); wrap.append(content); first.append(wrap);
      const meta = node("td"), metaWrap = node("div", "metadata");
      metaWrap.append(node("span", "chip", item.instrument_type === "stock" ? "股票" : item.instrument_type), node("span", "chip", item.market), node("span", "chip", item.frequency)); meta.append(metaWrap);
      const tags = node("td"), tagWrap = node("div", "tag-list"); (item.tags.length ? item.tags : ["未标记"]).forEach(tag => tagWrap.append(node("span", "chip", tag))); tags.append(tagWrap);
      const recent = node("td"), state = strategyStatus(item.last_run); recent.append(node("span", `run-state ${state.className}`, state.label));
      row.append(first, meta, tags, recent, node("td", "", String(item.run_count)), node("td", "", shortDate(item.updated_at))); body.append(row);
    }
  }
}
function renderStats() {
  $("strategy-total").textContent = String(strategies.length); $("strategy-active").textContent = String(strategies.filter(item => item.status === "active").length);
  $("strategy-archived").textContent = String(strategies.filter(item => item.status === "archived").length); $("run-total").textContent = String(strategies.reduce((sum, item) => sum + item.run_count, 0));
  $("run-running").textContent = String(strategies.filter(item => item.last_run?.status === "running").length);
}
async function loadStrategies() { if (isStrategyPage) return; strategies = await api<Strategy[]>("/api/strategies"); renderStats(); renderStrategyList(); }
function showView(name: "strategies") { if (name === "strategies") { clearTimeout(timer); loadStrategies().catch(reason => showError(errorMessage(reason))); } scrollTo({top: 0}); }
function setWorktab(name: WorkTab) {
  document.querySelectorAll<HTMLButtonElement>("[data-worktab]").forEach(button => button.classList.toggle("active", button.dataset.worktab === name));
  (["code", "results", "history"] as const).forEach(tab => $(`${tab}-pane`).hidden = tab !== name);
  if (name === "history") loadHistory().catch(reason => showError(errorMessage(reason)));
}
async function openStrategy(identifier: string) {
  try {
    showError(""); currentStrategy = await api<Strategy>(`/api/strategies/${identifier}`); dirty = false;
    $("strategy-title").textContent = currentStrategy.name; $("strategy-description").textContent = currentStrategy.description || "暂无策略说明"; setStrategyCode(currentStrategy.code);
    $<HTMLInputElement>("name").value = `${currentStrategy.name} · 回测`; $("save-state").hidden = true; document.title = `${currentStrategy.name} · QuantStone`;
    $("strategy-instrument").textContent = `股票 / ${currentStrategy.market}`; $<HTMLSelectElement>("frequency").value = currentStrategy.frequency === "分钟" ? "1m" : "1d";
    job = null; selected = null; resetResult(); setWorktab("code");
  } catch (reason) { showError(errorMessage(reason)); }
}
function markDirty() { dirty = true; $("save-state").textContent = "未保存"; $("save-state").hidden = false; $("save-state").classList.add("unsaved"); }
async function saveStrategy() {
  if (!currentStrategy) return; const payload = {...currentStrategy, code: getStrategyCode()};
  currentStrategy = await api<Strategy>(`/api/strategies/${currentStrategy.id}`, {method: "PUT", body: JSON.stringify(payload)});
  dirty = false; $("save-state").hidden = true; $("save-state").classList.remove("unsaved");
}
function resetResult() {
  $("status").textContent = "尚未开始"; $("result-name").textContent = "等待运行"; $<HTMLProgressElement>("progress").value = 0; $("progress-text").textContent = "";
  $("status").className = "status"; $("result-period").textContent = "尚未选择回测区间"; $("result-environment").textContent = "—"; $("chart-summary").textContent = "每日收盘后的账户净值";
  $("demo-warning").hidden = true;
  ["return-value", "drawdown-value", "equity-value", "trades-value", "latest-net-value", "initial-cash-value", "cash-value", "market-value", "realized-pnl-value", "unrealized-pnl-value", "fees-value"].forEach(id => { $(id).textContent = "—"; $(id).classList.remove("positive", "negative"); }); $("result-note").textContent = "";
  $<HTMLButtonElement>("cancel-button").hidden = true; $<HTMLButtonElement>("export-button").disabled = true; emptyChart("运行策略后，在这里查看账户净值。");
  $("detail-content").replaceChildren(node("p", "hint", "运行回测后查看明细。"));
}
function emptyChart(message: string) { const div = node("div", "empty"); div.append(node("strong", "", message)); $("chart").replaceChildren(div); }

function chart(rows: BacktestResult["equity_curve"]) {
  if (!rows.length) { emptyChart("暂无净值数据"); return; }
  const namespace = "http://www.w3.org/2000/svg", svg = document.createElementNS(namespace, "svg");
  svg.setAttribute("viewBox", "0 0 760 230"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "账户净值曲线");
  const make = (name: string, attrs: Record<string, string | number>, text?: string) => { const item = document.createElementNS(namespace, name); Object.entries(attrs).forEach(([key, value]) => item.setAttribute(key, String(value))); if (text !== undefined) item.textContent = text; svg.append(item); return item; };
  const defs = document.createElementNS(namespace, "defs"), gradient = document.createElementNS(namespace, "linearGradient"); gradient.id = "equity-area"; gradient.setAttribute("x1", "0"); gradient.setAttribute("y1", "0"); gradient.setAttribute("x2", "0"); gradient.setAttribute("y2", "1");
  [["0%", "#246bfe", ".22"], ["100%", "#246bfe", ".01"]].forEach(([offset, color, opacity]) => { const stop = document.createElementNS(namespace, "stop"); stop.setAttribute("offset", offset); stop.setAttribute("stop-color", color); stop.setAttribute("stop-opacity", opacity); gradient.append(stop); }); defs.append(gradient); svg.append(defs);
  const values = rows.map(row => Number(row.net_value)); let low = Math.min(1, ...values), high = Math.max(1, ...values); const padding = Math.max((high - low) * .15, .001); low -= padding; high += padding;
  const x = (index: number) => 62 + index / Math.max(rows.length - 1, 1) * 670, y = (value: number) => 180 - (value - low) / (high - low) * 145;
  for (let index = 0; index < 4; index++) { const value = low + (high - low) * index / 3; make("line", {x1: 62, y1: y(value), x2: 732, y2: y(value), stroke: "#e9eff6"}); make("text", {x: 52, y: y(value) + 4, "text-anchor": "end", "font-size": 11, fill: "#8a99ad"}, value.toFixed(3)); }
  make("line", {x1: 62, y1: y(1), x2: 732, y2: y(1), stroke: "#aab6c8", "stroke-dasharray": "4 5"});
  const path = values.map((value, index) => `${index ? "L" : "M"} ${x(index)} ${y(value)}`).join(" ");
  make("path", {d: `${path} L ${x(values.length - 1)} 180 L 62 180 Z`, fill: "url(#equity-area)"}); make("path", {d: path, fill: "none", stroke: "#246bfe", "stroke-width": 2.4, "stroke-linejoin": "round"});
  make("circle", {cx: x(values.length - 1), cy: y(values.at(-1)!), r: 4, fill: "#fff", stroke: "#246bfe", "stroke-width": 2.5});
  make("text", {x: 62, y: 215, "font-size": 11, fill: "#8a99ad"}, rows[0].date); make("text", {x: 732, y: 215, "text-anchor": "end", "font-size": 11, fill: "#8a99ad"}, rows.at(-1)!.date); $("chart").replaceChildren(svg);
  const guide = make("line", {x1: 62, y1: 30, x2: 62, y2: 180, stroke: "#94a3b8", opacity: 0}), marker = make("circle", {cx: 62, cy: 30, r: 4, fill: "#246bfe", stroke: "#fff", "stroke-width": 2, opacity: 0});
  const overlay = make("rect", {x: 62, y: 25, width: 670, height: 165, fill: "transparent"}), tooltip = node("div", "chart-tooltip"); $("chart").append(tooltip);
  overlay.addEventListener("pointermove", event => { const bounds = svg.getBoundingClientRect(), plotLeft = bounds.left + bounds.width * 62 / 760, plotWidth = bounds.width * 670 / 760; const index = Math.max(0, Math.min(rows.length - 1, Math.round((event.clientX - plotLeft) / plotWidth * (rows.length - 1)))); const pointX = x(index), pointY = y(values[index]); guide.setAttribute("x1", String(pointX)); guide.setAttribute("x2", String(pointX)); guide.setAttribute("opacity", "1"); marker.setAttribute("cx", String(pointX)); marker.setAttribute("cy", String(pointY)); marker.setAttribute("opacity", "1"); tooltip.textContent = `${rows[index].date}　净值 ${fmt(values[index], 4)}`; tooltip.style.left = `${pointX / 760 * 100}%`; tooltip.style.top = `${pointY / 230 * 100}%`; tooltip.classList.add("visible"); });
  overlay.addEventListener("pointerleave", () => { guide.setAttribute("opacity", "0"); marker.setAttribute("opacity", "0"); tooltip.classList.remove("visible"); });
}
function table<T extends Row>(container: HTMLElement, columns: TableColumn<T>[], rows: T[]) {
  container.replaceChildren(); if (!rows.length) { container.append(node("p", "hint", "暂无记录")); return; }
  const tableElement = node("table"), head = node("thead"), headerRow = node("tr"); columns.forEach(([title]) => headerRow.append(node("th", "", title))); head.append(headerRow); tableElement.append(head);
  const body = node("tbody");
  for (const row of rows) { const rowElement = node("tr"); for (const [, value] of columns) { const cell = node("td"), result = value(row); if (result instanceof Node) cell.append(result); else cell.textContent = result === null || result === undefined ? "—" : String(result); rowElement.append(cell); } body.append(rowElement); }
  tableElement.append(body); container.append(tableElement);
}
function detail() {
  const target = $("detail-content"), result = job?.result;
  if (activeTab === "logs") { const pre = node("pre"); pre.textContent = [...(result?.logs || []).map(entry => `${entry.time}  ${entry.message}`), job?.console || "", job?.error || ""].filter(Boolean).join("\n") || "暂无日志"; target.replaceChildren(pre); return; }
  const orderState: Record<string, string> = {accepted: "待成交", filled: "已成交", rejected: "已拒绝", cancelled: "已撤销", expired: "已到期"};
  if (activeTab === "positions") table(target, [["股票", row => row.symbol], ["持仓", row => row.quantity], ["可卖", row => row.sellable], ["成本价", row => fmt(row.average_cost, 4)], ["市值", row => fmt(row.market_value)], ["浮动盈亏", row => fmt(row.unrealized_pnl)]], result?.account.positions || []);
  if (activeTab === "orders") table(target, [["订单", row => row.id], ["股票", row => row.symbol], ["方向", row => row.side === "buy" ? "买入" : "卖出"], ["数量", row => row.quantity], ["状态", row => orderState[row.status] || row.status], ["提交时间", row => row.created_at], ["原因", row => row.reason]], result?.orders || []);
  if (activeTab === "trades") table(target, [["成交", row => row.id], ["股票", row => row.symbol], ["方向", row => row.side === "buy" ? "买入" : "卖出"], ["数量", row => row.quantity], ["价格", row => fmt(row.price)], ["费用", row => fmt(Object.values(row.fees as Record<string, string>).reduce((sum, value) => sum + Number(value), 0))], ["成交时间", row => row.timestamp]], result?.trades || []);
}
function render() {
  if (!job) return; $("status").textContent = states[job.status] || job.status; $("status").className = `status ${job.status}`; $("result-name").textContent = job.name;
  $("result-period").textContent = `${job.start} — ${job.end}`; $("result-environment").textContent = `${job.frequency === "1m" ? "1 分钟" : "日线"} · ${job.source === "demo" ? "合成测试行情（非真实）" : "真实行情"}`; $("demo-warning").hidden = job.source !== "demo";
  const progress = job.progress || {}; $<HTMLProgressElement>("progress").value = job.status === "completed" ? 1 : Math.min(1, (progress.completed || 0) / (progress.total || 1));
  $("progress-text").textContent = progress.total ? `${progress.stage === "loading" ? "加载数据" : "回放交易日"} ${progress.completed}/${progress.total} ${progress.date || ""}` : "";
  $<HTMLButtonElement>("cancel-button").hidden = job.status !== "running"; $<HTMLButtonElement>("export-button").disabled = !job.result;
  const result = job.result; $("return-value").textContent = result ? `${fmt(Number(result.metrics.total_return) * 100)}%` : "—"; $("drawdown-value").textContent = result ? `${fmt(Number(result.metrics.max_drawdown) * 100)}%` : "—";
  $("equity-value").textContent = result ? fmt(result.account.equity) : "—"; $("trades-value").textContent = result ? String(result.metrics.trade_count) : "—"; $("result-note").textContent = result?.data?.note || "";
  if (result) {
    const account = result.account, tone = (id: string, value: string | number) => $(id).classList.add(Number(value) >= 0 ? "positive" : "negative");
    $("latest-net-value").textContent = `最新 ${fmt(account.net_value, 4)}`; $("chart-summary").textContent = `${result.equity_curve.length} 个交易日 · 起始净值 1.0000`;
    $("initial-cash-value").textContent = fmt(account.initial_cash); $("cash-value").textContent = fmt(account.available_cash); $("market-value").textContent = fmt(account.market_value);
    $("realized-pnl-value").textContent = fmt(account.realized_pnl); $("unrealized-pnl-value").textContent = fmt(account.unrealized_pnl); $("fees-value").textContent = fmt(account.total_fees);
    tone("return-value", result.metrics.total_return); tone("realized-pnl-value", account.realized_pnl); tone("unrealized-pnl-value", account.unrealized_pnl);
  }
  if (result) chart(result.equity_curve); else emptyChart(job.status === "running" ? "正在加载数据或回放行情…" : job.error ? "运行失败，请查看日志。" : "没有可展示的结果。"); detail();
}
async function selectRun(id: string) {
  selected = id; clearTimeout(timer); const current = await api<Job>(`/api/runs/${id}`); if (selected !== id) return; job = current; render();
  if (job.status === "running") timer = setTimeout(() => selectRun(id).catch(reason => showError(errorMessage(reason))), 700); else { await loadStrategies(); await loadHistory(); }
}
async function loadHistory() {
  if (!currentStrategy) return; const rows = (await api<RunSummary[]>("/api/runs")).filter(item => item.strategy_id === currentStrategy!.id);
  table<RunSummary>($("history"), [["名称", row => row.name], ["品种 / 市场", row => `股票 / ${row.market || "A股"}`], ["数据", row => row.source === "demo" ? "合成测试（非真实）" : "真实数据"], ["频率", row => row.frequency], ["区间", row => `${row.start} — ${row.end}`], ["状态", row => states[row.status] || row.status], ["创建时间", row => new Date(row.created_at).toLocaleString("zh-CN")], ["操作", row => { const button = node("button", "secondary", "查看"); button.onclick = () => { setWorktab("results"); selectRun(row.id).catch(reason => showError(errorMessage(reason))); }; return button; }]], rows);
}

if (isStrategyPage) {
  $<HTMLFormElement>("run-form").onsubmit = async event => {
    event.preventDefault(); showError(""); $<HTMLButtonElement>("run-button").disabled = true; $<HTMLButtonElement>("run-button-top").disabled = true;
    try { if (dirty) await saveStrategy(); if (!currentStrategy) throw new Error("策略尚未载入");
      const config: Row = Object.fromEntries(fields.map(key => [key, formValue(key)])); Object.assign(config, {strategy_id: currentStrategy.id, instrument_type: currentStrategy.instrument_type, market: currentStrategy.market, strategy: getStrategyCode()});
      const created = await api<RunSummary>("/api/runs", {method: "POST", body: JSON.stringify(config)}); $<HTMLDialogElement>("config-dialog").close(); setWorktab("results"); await selectRun(created.id);
    } catch (reason) { showError(errorMessage(reason)); } finally { $<HTMLButtonElement>("run-button").disabled = false; $<HTMLButtonElement>("run-button-top").disabled = false; }
  };
  $<HTMLButtonElement>("run-button-top").onclick = () => $<HTMLFormElement>("run-form").requestSubmit();
  $<HTMLButtonElement>("config-button").onclick = () => { $<HTMLDialogElement>("config-dialog").showModal(); setTimeout(() => $<HTMLInputElement>("name").focus(), 0); };
  $<HTMLButtonElement>("config-close").onclick = () => $<HTMLDialogElement>("config-dialog").close();
  $<HTMLDialogElement>("config-dialog").onclick = event => { if (event.target === $("config-dialog")) $<HTMLDialogElement>("config-dialog").close(); };
  $<HTMLButtonElement>("save-strategy-button").onclick = () => { saveStrategy().catch(reason => showError(errorMessage(reason))); };
  $<HTMLButtonElement>("cancel-button").onclick = async () => { try { if (!selected) return; await api(`/api/runs/${selected}/cancel`, {method: "POST"}); await selectRun(selected); } catch (reason) { showError(errorMessage(reason)); } };
  $<HTMLButtonElement>("export-button").onclick = () => { if (!job?.result) return; const anchor = node("a"); anchor.href = URL.createObjectURL(new Blob([JSON.stringify(job, null, 2)], {type: "application/json"})); anchor.download = `quantstone-${job.id}.json`; anchor.click(); setTimeout(() => URL.revokeObjectURL(anchor.href), 1000); };
  $<HTMLButtonElement>("refresh-button").onclick = () => { loadHistory().catch(reason => showError(errorMessage(reason))); };
  $<HTMLTextAreaElement>("strategy").oninput = markDirty;
  document.addEventListener("quantstone-save", () => { saveStrategy().catch(reason => showError(errorMessage(reason))); }); document.addEventListener("quantstone-run", () => $<HTMLFormElement>("run-form").requestSubmit());
  $<HTMLAnchorElement>("back-button").onclick = event => { event.preventDefault(); if (dirty) $<HTMLDialogElement>("leave-dialog").showModal(); else location.assign("/"); };
  $<HTMLButtonElement>("leave-cancel").onclick = () => $<HTMLDialogElement>("leave-dialog").close(); $<HTMLButtonElement>("leave-confirm").onclick = () => { dirty = false; location.assign("/"); };
  $<HTMLDialogElement>("leave-dialog").onclick = event => { if (event.target === $("leave-dialog")) $<HTMLDialogElement>("leave-dialog").close(); };
  $<HTMLAnchorElement>("brand-home").onclick = event => { event.preventDefault(); $<HTMLAnchorElement>("back-button").click(); };
  window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
}

if (!isStrategyPage) {
  const goHome = () => location.assign("/"); $<HTMLAnchorElement>("brand-home").onclick = goHome; $<HTMLAnchorElement>("workspace-home").onclick = goHome; $<HTMLButtonElement>("research-home").onclick = goHome;
  function setSidebarCollapsed(collapsed: boolean) { document.body.classList.toggle("sidebar-collapsed", collapsed); const toggle = $<HTMLButtonElement>("sidebar-toggle"); toggle.setAttribute("aria-expanded", String(!collapsed)); toggle.setAttribute("aria-label", collapsed ? "展开侧栏" : "收起侧栏"); toggle.title = collapsed ? "展开侧栏" : "收起侧栏"; }
  let sidebarCollapsed = false; try { sidebarCollapsed = localStorage.getItem("quantstone.sidebar.collapsed") === "true"; } catch {} setSidebarCollapsed(sidebarCollapsed);
  $<HTMLButtonElement>("sidebar-toggle").onclick = () => { sidebarCollapsed = !document.body.classList.contains("sidebar-collapsed"); setSidebarCollapsed(sidebarCollapsed); try { localStorage.setItem("quantstone.sidebar.collapsed", String(sidebarCollapsed)); } catch {} };
  document.querySelectorAll<HTMLButtonElement>("[data-scope]").forEach(button => button.onclick = () => { scope = (button.dataset.scope || "all") as StrategyScope; $<HTMLSelectElement>("status-filter").value = ""; document.querySelectorAll<HTMLButtonElement>("[data-scope]").forEach(item => item.setAttribute("aria-pressed", String(item.dataset.scope === scope))); showView("strategies"); renderStrategyList(); });
  document.querySelectorAll<HTMLElement>("[data-coming]").forEach(item => item.onclick = event => { event.preventDefault(); showError("该模块将在后续版本开放。"); });
  ["strategy-search", "instrument-filter", "market-filter", "frequency-filter", "status-filter"].forEach(id => $(id).addEventListener(id === "strategy-search" ? "input" : "change", renderStrategyList));
  const dialog = $<HTMLDialogElement>("strategy-dialog");
  $<HTMLButtonElement>("new-strategy-button").onclick = () => { showError(""); $<HTMLFormElement>("strategy-form").reset(); dialog.showModal(); setTimeout(() => $<HTMLInputElement>("new-name").focus(), 0); };
  $<HTMLButtonElement>("dialog-close").onclick = () => dialog.close(); $<HTMLButtonElement>("dialog-cancel").onclick = () => dialog.close();
  $<HTMLFormElement>("strategy-form").onsubmit = async event => { event.preventDefault(); try {
    if (!exampleCode) exampleCode = (await api<{strategy: string}>("/api/example")).strategy;
    const created = await api<Strategy>("/api/strategies", {method: "POST", body: JSON.stringify({name: formValue("new-name").trim(), folder: formValue("new-folder").trim(), description: $<HTMLTextAreaElement>("new-description").value.trim(), instrument_type: formValue("new-instrument"), market: formValue("new-market"), frequency: formValue("new-frequency"), tags: formValue("new-tags").split(/[,，]/).map(tag => tag.trim()).filter(Boolean), code: exampleCode})});
    dialog.close(); await loadStrategies(); location.assign(`/strategies/${created.id}`);
  } catch (reason) { showError(errorMessage(reason)); } };
}

void (async () => { try { if (isStrategyPage) await openStrategy(location.pathname.split("/").pop() || ""); else { exampleCode = (await api<{strategy: string}>("/api/example")).strategy; await loadStrategies(); } } catch (reason) { showError(errorMessage(reason)); } })();
document.querySelectorAll<HTMLButtonElement>("[data-tab]").forEach(button => button.onclick = () => { activeTab = (button.dataset.tab || "positions") as DetailTab; document.querySelectorAll<HTMLButtonElement>("[data-tab]").forEach(item => item.setAttribute("aria-selected", String(item === button))); detail(); });
document.querySelectorAll<HTMLButtonElement>("[data-worktab]").forEach(button => button.onclick = () => setWorktab((button.dataset.worktab || "code") as WorkTab));

export {};
