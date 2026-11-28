type RunStatus = "running" | "completed" | "failed" | "cancelled" | "interrupted" | string;
type StrategyScope = "all" | "running" | "archived";
type DetailTab = "positions" | "trades" | "logs";
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
  metrics: {total_return: string | number; max_drawdown: string | number; trade_count: number; annualized_return?: number; annualized_volatility?: number; sharpe?: number; positive_day_rate?: number; trading_days?: number; max_drawdown_start?: string; max_drawdown_end?: string};
  account: Row & {initial_cash: string; available_cash: string; market_value: string; equity: string; realized_pnl: string; unrealized_pnl: string; total_pnl: string; total_fees: string; net_value: string; positions: Row[]};
  equity_curve: Array<{date: string; equity?: string; net_value: string}>;
  orders: Row[]; trades: Row[]; position_history?: Array<{date: string; positions: Row[]}>; logs: Array<{time: string; message: string}>; data?: {note?: string};
}
interface Job extends RunSummary {
  progress?: {stage?: string; completed?: number; total?: number; date?: string};
  result?: BacktestResult; preview?: {equity_curve: BacktestResult["equity_curve"]}; console?: string; error?: string;
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
let timer: ReturnType<typeof setTimeout> | undefined, activeTab: DetailTab = "positions", selectedDetailDate: string | null = null, exampleCode = "", dirty = false;
const chartRanges = new Map<string, {low: number; high: number}>(), dailyReturnLimits = new Map<string, number>();
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
  document.body.classList.toggle("result-mode", name === "results");
  document.querySelectorAll<HTMLButtonElement>("[data-worktab]").forEach(button => button.classList.toggle("active", button.dataset.worktab === name));
  (["code", "results", "history"] as const).forEach(tab => $(`${tab}-pane`).hidden = tab !== name);
  if (name === "results") showResultPage("overview");
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
  ["return-value", "annualized-return-value", "drawdown-value", "sharpe-value", "volatility-value", "positive-day-value", "equity-value", "trades-value", "latest-net-value"].forEach(id => { $(id).textContent = "—"; $(id).classList.remove("positive", "negative"); }); $("result-duration").textContent = "—"; $("result-note").textContent = "";
  $<HTMLButtonElement>("cancel-button").hidden = true; $<HTMLButtonElement>("export-button").disabled = true; emptyChart("运行策略后，在这里查看累计收益。", "chart"); emptyChart("暂无日收益数据", "daily-return-chart"); emptyChart("暂无成交记录", "trade-chart");
  $("detail-content").replaceChildren(node("p", "hint", "运行回测后查看明细。"));
}
function emptyChart(message: string, target = "chart") { const div = node("div", "empty"); div.append(node("strong", "", message)); $(target).replaceChildren(div); }

function svgChart(target: string, label: string, height: number) {
  const namespace = "http://www.w3.org/2000/svg", container = $(target), svg = document.createElementNS(namespace, "svg"), width = Math.max(680, container.clientWidth || 1000), canvasHeight = Math.max(height, container.clientHeight || height);
  svg.setAttribute("viewBox", `0 0 ${width} ${canvasHeight}`); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", label);
  const make = (name: string, attrs: Record<string, string | number>, text?: string) => { const item = document.createElementNS(namespace, name); Object.entries(attrs).forEach(([key, value]) => item.setAttribute(key, String(value))); if (text !== undefined) item.textContent = text; svg.append(item); return item; };
  container.replaceChildren(svg); return {svg, make, width, height: canvasHeight};
}
function fixedDateScale(left: number, right: number) {
  const start = Date.parse(`${job?.start || new Date().toISOString().slice(0, 10)}T00:00:00Z`), end = Date.parse(`${job?.end || job?.start || new Date().toISOString().slice(0, 10)}T00:00:00Z`), span = Math.max(86_400_000, end - start);
  return {start, end, span, x: (date: string) => left + Math.max(0, Math.min(1, (Date.parse(`${date}T00:00:00Z`) - start) / span)) * (right - left)};
}
function chart(rows: BacktestResult["equity_curve"], metrics?: BacktestResult["metrics"]) {
  const {svg, make, width, height} = svgChart("chart", "策略累计收益曲线", 330), left = 58, right = width - 15, top = 20, bottom = height - 38, scale = fixedDateScale(left, right);
  const values = rows.map(row => Number(row.net_value) - 1), key = job?.id || "empty", range = chartRanges.get(key) || {low: -.05, high: .05};
  if (values.length) { const observedLow = Math.min(0, ...values), observedHigh = Math.max(0, ...values), padding = Math.max((observedHigh - observedLow) * .12, .002); range.low = Math.min(range.low, observedLow - padding); range.high = Math.max(range.high, observedHigh + padding); chartRanges.set(key, range); }
  const y = (value: number) => bottom - (value - range.low) / (range.high - range.low) * (bottom - top);
  if (metrics?.max_drawdown_start && metrics.max_drawdown_end) { const startX=scale.x(metrics.max_drawdown_start),endX=scale.x(metrics.max_drawdown_end);make("rect",{x:startX,y:top,width:Math.max(2,endX-startX),height:bottom-top,fill:"#edf3fb"});make("text",{x:(startX+endX)/2,y:34,"text-anchor":"middle","font-size":10,fill:"#8392a8"},"最大回撤区间"); }
  for (let index = 0; index < 5; index++) { const value = range.low + (range.high-range.low)*index/4; make("line", {x1:left,y1:y(value),x2:right,y2:y(value),stroke:"#e6ebf1"}); make("text", {x:left-9,y:y(value)+4,"text-anchor":"end","font-size":10,fill:"#8592a5"}, `${fmt(value*100,1)}%`); }
  make("line", {x1:left,y1:y(0),x2:right,y2:y(0),stroke:"#aab4c2","stroke-dasharray":"4 4"});
  for (let index=0; index<6; index++) { const stamp=scale.start+scale.span*index/5,date=new Date(stamp).toISOString().slice(0,10),px=left+(right-left)*index/5;make("text",{x:px,y:height-10,"text-anchor":index===0?"start":index===5?"end":"middle","font-size":10,fill:"#8592a5"},date); }
  if (!rows.length) return;
  const path = values.map((value,index)=>`${index ? "L":"M"} ${scale.x(rows[index].date)} ${y(value)}`).join(" "); make("path", {d:path,fill:"none",stroke:"#df5a55","stroke-width":1.8,"stroke-linejoin":"round"});
  const guide=make("line",{x1:left,y1:top,x2:left,y2:bottom,stroke:"#718096",opacity:0}), marker=make("circle",{cx:left,cy:top,r:4,fill:"#df5a55",stroke:"#fff","stroke-width":2,opacity:0}), overlay=make("rect",{x:left,y:top,width:right-left,height:bottom-top,fill:"transparent"}), tooltip=node("div","chart-tooltip"); $("chart").append(tooltip);
  overlay.addEventListener("pointermove",event=>{const bounds=svg.getBoundingClientRect(),stamp=scale.start+Math.max(0,Math.min(1,((event.clientX-bounds.left)/bounds.width*width-left)/(right-left)))*scale.span;let index=0,best=Infinity;rows.forEach((row,rowIndex)=>{const distance=Math.abs(Date.parse(`${row.date}T00:00:00Z`)-stamp);if(distance<best){best=distance;index=rowIndex}});const px=scale.x(rows[index].date),py=y(values[index]);guide.setAttribute("x1",String(px));guide.setAttribute("x2",String(px));guide.setAttribute("opacity","1");marker.setAttribute("cx",String(px));marker.setAttribute("cy",String(py));marker.setAttribute("opacity","1");tooltip.textContent=`${rows[index].date}　累计收益 ${fmt(values[index]*100,2)}%`;tooltip.style.left=`${px/width*100}%`;tooltip.style.top=`${py/height*100}%`;tooltip.classList.add("visible")}); overlay.addEventListener("pointerleave",()=>{guide.setAttribute("opacity","0");marker.setAttribute("opacity","0");tooltip.classList.remove("visible")});
}
function dailyReturnChart(rows: BacktestResult["equity_curve"]) {
  const {make,width,height}=svgChart("daily-return-chart","每日收益",130), left=58,right=width-15,axis=height/2,amplitude=height/2-17,scale=fixedDateScale(left,right),key=job?.id||"empty"; const values=rows.map((row,index)=>index ? Number(row.net_value)/Number(rows[index-1].net_value)-1 : Number(row.net_value)-1),limit=Math.max(dailyReturnLimits.get(key)||.01,...values.map(Math.abs));dailyReturnLimits.set(key,limit);const y=(value:number)=>axis-value/limit*amplitude,dayCount=Math.max(1,Math.round(scale.span/86_400_000)+1),barWidth=Math.max(.8,(right-left)/dayCount*.72);
  make("line",{x1:left,y1:axis,x2:right,y2:axis,stroke:"#aeb8c7"}); values.forEach((value,index)=>{const px=scale.x(rows[index].date),py=y(value);const bar=make("rect",{x:px-barWidth/2,y:Math.min(axis,py),width:barWidth,height:Math.max(1,Math.abs(py-axis)),fill:value>=0?"#e95a55":"#4fbd64"}); const title=document.createElementNS("http://www.w3.org/2000/svg","title");title.textContent=`${rows[index].date} ${fmt(value*100,2)}%`;bar.append(title)}); make("text",{x:left-9,y:12,"text-anchor":"end","font-size":9,fill:"#8592a5"},`${fmt(limit*100,1)}%`);make("text",{x:left-9,y:height-6,"text-anchor":"end","font-size":9,fill:"#8592a5"},`-${fmt(limit*100,1)}%`);
}
function tradeChart(trades: Row[], start: string, end: string) {
  if (!trades.length) { emptyChart("当前回测没有成交记录", "trade-chart"); return; }
  const {make,width,height:canvasHeight}=svgChart("trade-chart","成交记录",120),left=58,right=width-15,axis=canvasHeight/2-5; const startTime=new Date(start).getTime(), endTime=new Date(end).getTime(), notionals=trades.map(item=>Number(item.price)*Number(item.quantity)),maximum=Math.max(...notionals,1),barWidth=Math.max(2,Math.min(8,(right-left)/trades.length*.5)); make("line",{x1:left,y1:axis,x2:right,y2:axis,stroke:"#b7c0cd"}); trades.forEach((item,index)=>{const time=new Date(item.timestamp).getTime(),px=left+(time-startTime)/Math.max(1,endTime-startTime)*(right-left),barHeight=(axis-8)*notionals[index]/maximum,buy=item.side==="buy",bar=make("rect",{x:px-barWidth/2,y:buy?axis-barHeight:axis,width:barWidth,height:barHeight,fill:buy?"#e95a55":"#4d8dd9"});const title=document.createElementNS("http://www.w3.org/2000/svg","title");title.textContent=`${item.timestamp} ${buy?"买入":"卖出"} ${item.symbol} ${fmt(notionals[index])} 元`;bar.append(title)}); make("text",{x:left,y:canvasHeight-5,"font-size":9,fill:"#8592a5"},start);make("text",{x:right,y:canvasHeight-5,"text-anchor":"end","font-size":9,fill:"#8592a5"},end);
}
function renderResultCharts(result: BacktestResult) { if (!job) return; chart(result.equity_curve, result.metrics); dailyReturnChart(result.equity_curve); tradeChart(result.trades, job.start, job.end); }
function calculatePreviewMetrics(rows: BacktestResult["equity_curve"]): BacktestResult["metrics"] {
  const values=rows.map(row=>Number(row.net_value)), returns=values.slice(1).map((value,index)=>value/values[index]-1), mean=returns.length?returns.reduce((sum,value)=>sum+value,0)/returns.length:0;
  const volatility=returns.length>1?Math.sqrt(returns.reduce((sum,value)=>sum+(value-mean)**2,0)/returns.length):0; let peak=1, peakDate=rows[0]?.date, drawdown=0, drawdownStart=peakDate, drawdownEnd=peakDate;
  rows.forEach((row,index)=>{if(values[index]>peak){peak=values[index];peakDate=row.date}const current=(peak-values[index])/peak;if(current>drawdown){drawdown=current;drawdownStart=peakDate;drawdownEnd=row.date}});
  const periods=Math.max(returns.length,1), total=(values.at(-1)??1)-1;
  return {total_return:total,annualized_return:(values.at(-1)??1)**(252/periods)-1,annualized_volatility:volatility*Math.sqrt(252),sharpe:volatility?mean/volatility*Math.sqrt(252):0,positive_day_rate:returns.length?returns.filter(value=>value>0).length/returns.length:0,max_drawdown:drawdown,max_drawdown_start:drawdownStart,max_drawdown_end:drawdownEnd,trade_count:0,trading_days:rows.length};
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
  const toolbar=$("detail-date-toolbar"),dateInput=$<HTMLInputElement>("detail-date"),allButton=$<HTMLButtonElement>("detail-date-all");
  if (activeTab === "logs") { toolbar.hidden=true;const count=(result?.logs.length||0)+(job?.console?1:0)+(job?.error?1:0);$("result-detail-count").textContent=`${count} 条记录`;const pre=node("pre");pre.textContent=[...(result?.logs||[]).map(entry=>`${entry.time}  ${entry.message}`),job?.console||"",job?.error||""].filter(Boolean).join("\n")||"暂无日志";target.replaceChildren(pre);return; }
  toolbar.hidden=false;allButton.hidden=activeTab!=="trades";
  const history=result?.position_history?.length?result.position_history:(result?[{date:job?.end||"",positions:result.account.positions}]:[]),dates=activeTab==="trades"?[...new Set((result?.trades||[]).map(row=>String(row.timestamp).slice(0,10)))].sort():history.map(row=>row.date),fallback=dates.at(-1)||job?.end||"";
  if (activeTab==="positions"&&!selectedDetailDate) selectedDetailDate=fallback;
  if (selectedDetailDate&&dates.length&&!dates.includes(selectedDetailDate)) selectedDetailDate=dates.reduce((best,date)=>Math.abs(Date.parse(date)-Date.parse(selectedDetailDate!))<Math.abs(Date.parse(best)-Date.parse(selectedDetailDate!))?date:best,dates[0]);
  dateInput.min=dates[0]||job?.start||"";dateInput.max=dates.at(-1)||job?.end||"";dateInput.value=selectedDetailDate||"";
  const dateIndex=selectedDetailDate?dates.indexOf(selectedDetailDate):-1;$<HTMLButtonElement>("detail-date-previous").disabled=dateIndex<=0;$<HTMLButtonElement>("detail-date-next").disabled=dateIndex<0||dateIndex>=dates.length-1;
  $("detail-date-summary").textContent=selectedDetailDate?`正在查看 ${selectedDetailDate}`:"正在查看全部日期";
  if (activeTab==="positions") { const positions=history.find(row=>row.date===selectedDetailDate)?.positions||[];$("result-detail-count").textContent=`${positions.length} 只股票`;table(target,[["股票",row=>row.symbol],["持仓",row=>row.quantity],["可卖",row=>row.sellable],["成本价",row=>fmt(row.average_cost,4)],["收盘价",row=>fmt(row.price,4)],["市值",row=>fmt(row.market_value)],["浮动盈亏",row=>fmt(row.unrealized_pnl)]],positions); }
  if (activeTab==="trades") { const trades=(result?.trades||[]).filter(row=>!selectedDetailDate||String(row.timestamp).slice(0,10)===selectedDetailDate);$("result-detail-count").textContent=`${trades.length} 笔成交`;table(target,[["成交日期",row=>String(row.timestamp).slice(0,10)],["时间",row=>new Date(row.timestamp).toLocaleTimeString("zh-CN",{hour12:false})],["股票",row=>row.symbol],["方向",row=>row.side==="buy"?"买入":"卖出"],["数量",row=>row.quantity],["价格",row=>fmt(row.price)],["费用",row=>fmt(Object.values(row.fees as Record<string,string>).reduce((sum,value)=>sum+Number(value),0))]],trades); }
}
function render() {
  if (!job) return; $("status").textContent = states[job.status] || job.status; $("status").className = `status ${job.status}`; $("result-name").textContent = job.name;
  $("result-period").textContent = `${job.start} — ${job.end}`; $("result-environment").textContent = `${job.frequency === "1m" ? "1 分钟" : "日线"} · ${job.source === "demo" ? "合成测试行情（非真实）" : "真实行情"}`; $("demo-warning").hidden = job.source !== "demo";
  const progress = job.progress || {}; $<HTMLProgressElement>("progress").value = job.status === "completed" ? 1 : Math.min(1, (progress.completed || 0) / (progress.total || 1));
  $("progress-text").textContent = job.status === "running" && progress.total ? `${progress.stage === "loading" ? "加载数据" : "回放交易日"} ${progress.completed}/${progress.total} ${progress.date || ""}` : "";
  $<HTMLButtonElement>("cancel-button").hidden = job.status !== "running"; $<HTMLButtonElement>("export-button").disabled = !job.result;
  const result = job.result, previewRows = job.preview?.equity_curve || [], metrics = result?.metrics || (previewRows.length ? calculatePreviewMetrics(previewRows) : undefined), percent = (value?: string | number) => value === undefined || value === null ? "—" : `${fmt(Number(value) * 100)}%`;
  ["return-value","annualized-return-value"].forEach(id=>$(id).classList.remove("positive","negative"));
  $("return-value").textContent = metrics ? percent(metrics.total_return) : "—"; $("annualized-return-value").textContent = metrics ? percent(metrics.annualized_return) : "—"; $("drawdown-value").textContent = metrics ? percent(metrics.max_drawdown) : "—";
  $("sharpe-value").textContent = metrics?.sharpe === undefined ? "—" : fmt(metrics.sharpe, 2); $("volatility-value").textContent = metrics ? percent(metrics.annualized_volatility) : "—"; $("positive-day-value").textContent = metrics ? percent(metrics.positive_day_rate) : "—";
  $("equity-value").textContent = result ? fmt(result.account.equity) : previewRows.at(-1)?.equity ? fmt(previewRows.at(-1)!.equity!) : "—"; $("trades-value").textContent = result ? String(result.metrics.trade_count) : "—"; $("result-duration").textContent = metrics?.trading_days ? `${metrics.trading_days} 个交易日` : "—"; $("result-note").textContent = result?.data?.note || (previewRows.length ? "实时预览数据；回测完成后生成正式账户与成交结果。" : "");
  if (result) {
    const account = result.account, tone = (id: string, value: string | number) => $(id).classList.add(Number(value) >= 0 ? "positive" : "negative");
    $("latest-net-value").textContent = `最新 ${fmt(account.net_value, 4)}`; $("chart-summary").textContent = `${result.equity_curve.length} 个交易日 · 起始净值 1.0000`;
    tone("return-value", result.metrics.total_return); if (result.metrics.annualized_return !== undefined) tone("annualized-return-value", result.metrics.annualized_return);
  }
  if (result) renderResultCharts(result);
  else if (previewRows.length && metrics) { chart(previewRows,metrics); dailyReturnChart(previewRows); emptyChart("成交记录将在回测完成后显示", "trade-chart"); $("latest-net-value").textContent=`实时 ${fmt(previewRows.at(-1)!.net_value,4)}`; $("chart-summary").textContent=`已回放 ${previewRows.length} / ${progress.total || "—"} 个交易日`; const tone=(id:string,value:string|number)=>$(id).classList.add(Number(value)>=0?"positive":"negative");tone("return-value",metrics.total_return);if(metrics.annualized_return!==undefined)tone("annualized-return-value",metrics.annualized_return); }
  else { chart([], undefined); dailyReturnChart([]); emptyChart("暂无成交记录", "trade-chart"); } detail();
}
async function selectRun(id: string) {
  selected = id; clearTimeout(timer); const current = await api<Job>(`/api/runs/${id}`); if (selected !== id) return; job = current; render();
  if (job.status === "running") {
    timer = setTimeout(() => selectRun(id).catch(reason => showError(errorMessage(reason))), 30);
  } else { await loadStrategies(); await loadHistory(); }
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
function setDetailTab(tab: DetailTab) { activeTab = tab; selectedDetailDate = null; detail(); }
const resultPageCopy: Record<string, [string, string]> = {trades:["交易明细","按日期快速查询撮合引擎产生的实际成交"],positions:["历史持仓","按交易日查看每日收盘后的证券持仓"],logs:["输出日志","策略日志、复权调整、标准输出及运行错误"]};
function showResultPage(target: string) {
  const overview = target === "overview"; $("result-overview").hidden = !overview; $("result-details").hidden = overview;
  document.querySelectorAll<HTMLButtonElement>("[data-result-target]").forEach(item => item.classList.toggle("active", item.dataset.resultTarget === target));
  if (!overview) { const [title, description] = resultPageCopy[target] || resultPageCopy.trades; $("result-detail-title").textContent = title; $("result-detail-description").textContent = description; setDetailTab(target as DetailTab); }
  else if (job) requestAnimationFrame(render);
  scrollTo({top:0,behavior:"auto"});
}
document.querySelectorAll<HTMLButtonElement>("[data-result-target]").forEach(button => button.onclick = () => showResultPage(button.dataset.resultTarget || "overview"));
function availableDetailDates() { if (!job?.result) return [] as string[]; return activeTab==="trades"?[...new Set(job.result.trades.map(row=>String(row.timestamp).slice(0,10)))].sort():(job.result.position_history||[]).map(row=>row.date); }
$<HTMLInputElement>("detail-date").onchange=event=>{selectedDetailDate=(event.currentTarget as HTMLInputElement).value||null;detail();};
$<HTMLButtonElement>("detail-date-all").onclick=()=>{selectedDetailDate=null;detail();};
$<HTMLButtonElement>("detail-date-previous").onclick=()=>{const dates=availableDetailDates(),index=selectedDetailDate?dates.indexOf(selectedDetailDate):-1;if(index>0){selectedDetailDate=dates[index-1];detail();}};
$<HTMLButtonElement>("detail-date-next").onclick=()=>{const dates=availableDetailDates(),index=selectedDetailDate?dates.indexOf(selectedDetailDate):-1;if(index>=0&&index<dates.length-1){selectedDetailDate=dates[index+1];detail();}};
document.querySelectorAll<HTMLButtonElement>("[data-worktab]").forEach(button => button.onclick = () => setWorktab((button.dataset.worktab || "code") as WorkTab));
let resultResizeTimer: ReturnType<typeof setTimeout> | undefined;
window.addEventListener("resize", () => { if (!isStrategyPage || !job || $("result-overview").hidden) return; clearTimeout(resultResizeTimer); resultResizeTimer = setTimeout(render, 120); });

export {};
