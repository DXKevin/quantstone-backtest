'use strict';
const $ = id => document.getElementById(id);
const states = {running:'运行中',completed:'已完成',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
const fields = ['name','source','frequency','minute_label','start','end','initial_cash','commission_rate','minimum_commission','sell_tax_rate','transfer_rate'];
let strategies = [], currentStrategy = null, selected = null, job = null, timer = null, activeTab = 'positions', exampleCode = '', dirty = false;
let scope = 'all';

function error(message){$('notice').textContent=message;$('notice').hidden=!message;if(message)scrollTo({top:0,behavior:'smooth'});}
async function api(path, options={}){
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Quantstone-Client':'workbench',...options.headers}});
  const result=await response.json();
  if(!response.ok){let detail=result.detail;if(Array.isArray(detail))detail=detail.map(x=>`${x.loc.join('.')}: ${x.msg}`).join('\n');throw Error(detail||'请求失败');}
  return result;
}
const fmt=(v,places=2)=>Number(v).toLocaleString('zh-CN',{minimumFractionDigits:places,maximumFractionDigits:places});
const shortDate=value=>value?new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'—';
function node(tag,className,text){const element=document.createElement(tag);if(className)element.className=className;if(text!==undefined)element.textContent=text;return element;}

function strategyStatus(lastRun){
  if(!lastRun)return {label:'尚未回测',className:''};
  return {label:states[lastRun.status]||lastRun.status,className:lastRun.status};
}
function renderStrategyList(){
  const query=$('strategy-search').value.trim().toLowerCase(),market=$('market-filter').value,frequency=$('frequency-filter').value,status=$('status-filter').value;
  const filtered=strategies.filter(item=>{
    const haystack=[item.name,item.description,...item.tags].join(' ').toLowerCase();
    const inScope=scope==='all'||(scope==='running'?item.last_run?.status==='running':item.status==='archived');
    return inScope&&(!query||haystack.includes(query))&&(!market||item.market===market)&&(!frequency||item.frequency===frequency)&&(!status||item.status===status);
  });
  const body=$('strategy-list');body.replaceChildren();$('strategy-empty').hidden=filtered.length>0;
  $('list-count').textContent=`显示 ${filtered.length} 个策略 / 共 ${strategies.length} 个`;
  for(const item of filtered){
    const tr=document.createElement('tr');
    const first=document.createElement('td'),wrap=node('div','strategy-name'),content=document.createElement('div'),open=document.createElement('button');
    open.textContent=item.name;open.onclick=()=>openStrategy(item.id);content.append(open,node('p','',item.description||'暂无策略说明'));wrap.append(content);first.append(wrap);
    const meta=document.createElement('td'),metaWrap=node('div','metadata');metaWrap.append(node('span','chip blue',item.market),node('span','chip',item.frequency));meta.append(metaWrap);
    const tags=document.createElement('td'),tagWrap=node('div','tag-list');(item.tags.length?item.tags:['未标记']).forEach(tag=>tagWrap.append(node('span','chip',tag)));tags.append(tagWrap);
    const recent=document.createElement('td'),state=strategyStatus(item.last_run),stateNode=node('span',`run-state ${state.className}`,state.label);recent.append(stateNode);
    const count=node('td','',String(item.run_count));
    const updated=node('td','',shortDate(item.updated_at));
    const action=document.createElement('td'),button=node('button','row-action','打开');button.onclick=()=>openStrategy(item.id);action.append(button);
    tr.append(first,meta,tags,recent,count,updated,action);body.append(tr);
  }
}
function renderStats(){
  $('strategy-total').textContent=strategies.length;
  $('strategy-active').textContent=strategies.filter(x=>x.status==='active').length;
  $('strategy-archived').textContent=strategies.filter(x=>x.status==='archived').length;
  $('run-total').textContent=strategies.reduce((sum,x)=>sum+x.run_count,0);
  $('run-running').textContent=strategies.filter(x=>x.last_run?.status==='running').length;
}
async function loadStrategies(){
  strategies=await api('/api/strategies');renderStats();renderStrategyList();
}
function showView(name){
  $('strategy-view').hidden=name!=='strategies';$('workbench-view').hidden=name!=='workbench';
  if(name==='strategies'){clearTimeout(timer);loadStrategies().catch(e=>error(e.message));}
  scrollTo({top:0});
}
function setWorktab(name){
  document.querySelectorAll('[data-worktab]').forEach(button=>button.classList.toggle('active',button.dataset.worktab===name));
  ['code','results','history'].forEach(tab=>$(`${tab}-pane`).hidden=tab!==name);
  if(name==='history')history().catch(e=>error(e.message));
}
async function openStrategy(identifier){
  try{
    error('');currentStrategy=await api(`/api/strategies/${identifier}`);dirty=false;
    $('strategy-title').textContent=currentStrategy.name;$('strategy-description').textContent=currentStrategy.description||'暂无策略说明';
    $('strategy-status-dot').classList.toggle('archived',currentStrategy.status==='archived');
    $('strategy').value=currentStrategy.code;$('name').value=`${currentStrategy.name} · 回测`;$('save-state').textContent='已保存';
    job=null;selected=null;resetResult();showView('workbench');setWorktab('code');
  }catch(e){error(e.message);}
}
function markDirty(){dirty=true;$('save-state').textContent='有未保存修改';}
async function saveStrategy(){
  if(!currentStrategy)return;
  const payload={name:currentStrategy.name,description:currentStrategy.description,market:currentStrategy.market,frequency:currentStrategy.frequency,tags:currentStrategy.tags,code:$('strategy').value,status:currentStrategy.status};
  currentStrategy=await api(`/api/strategies/${currentStrategy.id}`,{method:'PUT',body:JSON.stringify(payload)});
  dirty=false;$('save-state').textContent='已保存';
}
function resetResult(){
  $('status').textContent='尚未开始';$('result-name').textContent='等待运行';$('progress').value=0;$('progress-text').textContent='';
  ['return-value','drawdown-value','equity-value','trades-value'].forEach(id=>$(id).textContent='—');
  $('result-note').textContent='';$('cancel-button').hidden=true;$('export-button').disabled=true;emptyChart('运行策略后，在这里查看账户净值。');
  $('detail-content').replaceChildren(node('p','hint','运行回测后查看明细。'));
}
function emptyChart(message){const div=node('div','empty');div.append(node('strong','',message));$('chart').replaceChildren(div);}
function chart(rows){
  if(!rows.length){emptyChart('暂无净值数据');return;}
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 760 230');svg.setAttribute('role','img');svg.setAttribute('aria-label','账户净值曲线');
  const make=(name,attrs,text)=>{const item=document.createElementNS(ns,name);Object.entries(attrs).forEach(([k,v])=>item.setAttribute(k,v));if(text!==undefined)item.textContent=text;svg.append(item);return item;};
  const values=rows.map(r=>Number(r.net_value));let lo=Math.min(1,...values),hi=Math.max(1,...values);const pad=Math.max((hi-lo)*.15,.001);lo-=pad;hi+=pad;
  const x=i=>62+i/Math.max(rows.length-1,1)*670,y=v=>180-(v-lo)/(hi-lo)*145;
  for(let i=0;i<4;i++){const v=lo+(hi-lo)*i/3;make('line',{x1:62,y1:y(v),x2:732,y2:y(v),stroke:'#e9eff6'});make('text',{x:52,y:y(v)+4,'text-anchor':'end','font-size':11,fill:'#8a99ad'},v.toFixed(3));}
  const path=values.map((v,i)=>`${i?'L':'M'} ${x(i)} ${y(v)}`).join(' ');make('path',{d:`${path} L ${x(values.length-1)} 180 L 62 180 Z`,fill:'#edf4ff'});make('path',{d:path,fill:'none',stroke:'#246bfe','stroke-width':2.4,'stroke-linejoin':'round'});
  make('text',{x:62,y:215,'font-size':11,fill:'#8a99ad'},rows[0].date);make('text',{x:732,y:215,'text-anchor':'end','font-size':11,fill:'#8a99ad'},rows.at(-1).date);$('chart').replaceChildren(svg);
}
function table(container,columns,rows){
  container.replaceChildren();if(!rows.length){container.append(node('p','hint','暂无记录'));return;}
  const t=document.createElement('table'),head=document.createElement('thead'),hr=document.createElement('tr');columns.forEach(([title])=>hr.append(node('th','',title)));head.append(hr);t.append(head);
  const body=document.createElement('tbody');for(const row of rows){const tr=document.createElement('tr');for(const [,value] of columns){const td=document.createElement('td'),result=value(row);if(result instanceof Node)td.append(result);else td.textContent=result??'—';tr.append(td);}body.append(tr);}t.append(body);container.append(t);
}
function detail(){
  const target=$('detail-content'),result=job?.result;
  if(activeTab==='logs'){const pre=document.createElement('pre');pre.textContent=[...(result?.logs||[]).map(x=>`${x.time}  ${x.message}`),job?.console||'',job?.error||''].filter(Boolean).join('\n')||'暂无日志';target.replaceChildren(pre);return;}
  const orderState={accepted:'待成交',filled:'已成交',rejected:'已拒绝',cancelled:'已撤销',expired:'已到期'};
  if(activeTab==='positions')table(target,[['股票',r=>r.symbol],['持仓',r=>r.quantity],['可卖',r=>r.sellable],['成本价',r=>fmt(r.average_cost,4)],['市值',r=>fmt(r.market_value)],['浮动盈亏',r=>fmt(r.unrealized_pnl)]],result?.account.positions||[]);
  if(activeTab==='orders')table(target,[['订单',r=>r.id],['股票',r=>r.symbol],['方向',r=>r.side==='buy'?'买入':'卖出'],['数量',r=>r.quantity],['状态',r=>orderState[r.status]||r.status],['提交时间',r=>r.created_at],['原因',r=>r.reason]],result?.orders||[]);
  if(activeTab==='trades')table(target,[['成交',r=>r.id],['股票',r=>r.symbol],['方向',r=>r.side==='buy'?'买入':'卖出'],['数量',r=>r.quantity],['价格',r=>fmt(r.price)],['费用',r=>fmt(Object.values(r.fees).reduce((a,b)=>a+Number(b),0))],['成交时间',r=>r.timestamp]],result?.trades||[]);
}
function render(){
  $('status').textContent=states[job.status]||job.status;$('result-name').textContent=job.name;
  const p=job.progress||{};$('progress').value=job.status==='completed'?1:Math.min(1,(p.completed||0)/(p.total||1));
  $('progress-text').textContent=p.total?`${p.stage==='loading'?'加载数据':'回放交易日'} ${p.completed}/${p.total} ${p.date||''}`:'';
  $('cancel-button').hidden=job.status!=='running';$('export-button').disabled=!job.result;
  const r=job.result;$('return-value').textContent=r?`${fmt(Number(r.metrics.total_return)*100)}%`:'—';$('drawdown-value').textContent=r?`${fmt(Number(r.metrics.max_drawdown)*100)}%`:'—';$('equity-value').textContent=r?fmt(r.account.equity):'—';$('trades-value').textContent=r?r.metrics.trade_count:'—';$('result-note').textContent=r?.data.note||'';
  if(r)chart(r.equity_curve);else emptyChart(job.status==='running'?'正在加载数据或回放行情…':job.error?'运行失败，请查看日志。':'没有可展示的结果。');detail();
}
async function select(id){
  selected=id;clearTimeout(timer);const current=await api(`/api/runs/${id}`);if(selected!==id)return;job=current;render();
  if(job.status==='running')timer=setTimeout(()=>select(id).catch(e=>error(e.message)),700);else{await loadStrategies();await history();}
}
async function history(){
  if(!currentStrategy)return;const rows=(await api('/api/runs')).filter(item=>item.strategy_id===currentStrategy.id);
  table($('history'),[['名称',r=>r.name],['数据',r=>r.source==='demo'?'合成示例':'真实数据'],['频率',r=>r.frequency],['区间',r=>`${r.start} — ${r.end}`],['状态',r=>states[r.status]],['创建时间',r=>new Date(r.created_at).toLocaleString('zh-CN')],['操作',r=>{const b=node('button','secondary','查看');b.onclick=()=>{setWorktab('results');select(r.id).catch(e=>error(e.message));};return b;}]],rows);
}

$('run-form').onsubmit=async event=>{
  event.preventDefault();error('');$('run-button').disabled=true;$('run-button-top').disabled=true;
  try{
    if(dirty)await saveStrategy();
    const config=Object.fromEntries(fields.map(k=>[k,$(k).value]));config.strategy_id=currentStrategy.id;config.strategy=$('strategy').value;config.symbols=$('symbols').value.split(/[,，\s]+/).filter(Boolean);
    const created=await api('/api/runs',{method:'POST',body:JSON.stringify(config)});setWorktab('results');await select(created.id);
  }catch(e){error(e.message);}finally{$('run-button').disabled=false;$('run-button-top').disabled=false;}
};
$('run-button-top').onclick=()=>{$('run-form').requestSubmit();};
$('save-strategy-button').onclick=()=>saveStrategy().catch(e=>error(e.message));
$('cancel-button').onclick=async()=>{try{await api(`/api/runs/${selected}/cancel`,{method:'POST'});await select(selected);}catch(e){error(e.message);}};
$('export-button').onclick=()=>{if(!job?.result)return;const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(job,null,2)],{type:'application/json'}));a.download=`quantstone-${job.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};
$('refresh-button').onclick=()=>history().catch(e=>error(e.message));
$('example-button').onclick=async()=>{if($('strategy').value&&!confirm('载入示例将替换当前代码，是否继续？'))return;$('strategy').value=exampleCode||(await api('/api/example')).strategy;markDirty();};
$('strategy').oninput=markDirty;$('strategy').onkeydown=e=>{if(e.key==='Tab'){e.preventDefault();e.target.setRangeText('    ',e.target.selectionStart,e.target.selectionEnd,'end');markDirty();}};
$('source').onchange=()=>{$('source-note').textContent=$('source').value==='demo'?'示例是合成数据，用于验证流程。':'读取本机 QuantStone 数据服务。';};
$('help-button').onclick=()=>{$('help').open=!$('help').open;};
$('back-button').onclick=()=>{if(dirty&&!confirm('当前代码尚未保存，仍要返回策略列表吗？'))return;showView('strategies');};
$('brand-home').onclick=event=>{event.preventDefault();$('back-button').click();};
$('workspace-home').onclick=$('brand-home').onclick;
$('research-home').onclick=()=>$('back-button').click();
document.querySelectorAll('[data-scope]').forEach(button=>button.onclick=()=>{
  if(dirty&&!$('workbench-view').hidden&&!confirm('当前代码尚未保存，仍要返回策略列表吗？'))return;
  scope=button.dataset.scope;
  $('status-filter').value='';
  document.querySelectorAll('[data-scope]').forEach(item=>item.setAttribute('aria-pressed',String(item.dataset.scope===scope)));
  showView('strategies');renderStrategyList();
});
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{activeTab=b.dataset.tab;document.querySelectorAll('[data-tab]').forEach(x=>x.setAttribute('aria-selected',String(x===b)));detail();});
document.querySelectorAll('[data-worktab]').forEach(b=>b.onclick=()=>setWorktab(b.dataset.worktab));
document.querySelectorAll('[data-coming]').forEach(item=>item.onclick=event=>{event.preventDefault();error('该模块将在后续版本开放。');});
['strategy-search','market-filter','frequency-filter','status-filter'].forEach(id=>$(id).addEventListener(id==='strategy-search'?'input':'change',renderStrategyList));

const dialog=$('strategy-dialog');
$('new-strategy-button').onclick=()=>{error('');$('strategy-form').reset();dialog.showModal();setTimeout(()=>$('new-name').focus(),0);};
$('dialog-close').onclick=$('dialog-cancel').onclick=()=>dialog.close();
$('strategy-form').onsubmit=async event=>{
  event.preventDefault();
  try{
    if(!exampleCode)exampleCode=(await api('/api/example')).strategy;
    const created=await api('/api/strategies',{method:'POST',body:JSON.stringify({name:$('new-name').value.trim(),description:$('new-description').value.trim(),market:$('new-market').value,frequency:$('new-frequency').value,tags:$('new-tags').value.split(/[,，]/).map(x=>x.trim()).filter(Boolean),code:exampleCode})});
    dialog.close();await loadStrategies();await openStrategy(created.id);
  }catch(e){error(e.message);}
};

(async()=>{try{exampleCode=(await api('/api/example')).strategy;await loadStrategies();}catch(e){error(e.message);}})();
