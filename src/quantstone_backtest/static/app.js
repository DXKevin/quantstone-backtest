'use strict';
const $ = id => document.getElementById(id);
let selected = null, job = null, timer = null, activeTab = 'positions';
const states = {running:'运行中',completed:'已完成',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
const fields = ['name','source','frequency','minute_label','start','end','initial_cash','commission_rate','minimum_commission','sell_tax_rate','transfer_rate'];
const fmt = (v, places=2) => Number(v).toLocaleString('zh-CN',{minimumFractionDigits:places,maximumFractionDigits:places});
function error(message){ $('notice').textContent = message; $('notice').hidden = !message; }
async function api(path, options={}){
  const response = await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Quantstone-Client':'workbench',...options.headers}});
  const result = await response.json();
  if(!response.ok){let detail = result.detail; if(Array.isArray(detail)) detail=detail.map(x=>`${x.loc.join('.')}: ${x.msg}`).join('\n'); throw Error(detail||'请求失败');}
  return result;
}
function emptyChart(message){$('chart').replaceChildren();const div=document.createElement('div');div.className='empty';const p=document.createElement('p');p.textContent=message;div.append(p);$('chart').append(div);}
function chart(rows){
  if(!rows.length){emptyChart('暂无净值数据');return;}
  const ns='http://www.w3.org/2000/svg', svg=document.createElementNS(ns,'svg'); svg.setAttribute('viewBox','0 0 640 185');svg.setAttribute('role','img');svg.setAttribute('aria-label','每日账户净值曲线');
  const make=(name,attrs,text)=>{const node=document.createElementNS(ns,name);Object.entries(attrs).forEach(([k,v])=>node.setAttribute(k,v));if(text!==undefined)node.textContent=text;svg.append(node);return node;};
  const values=rows.map(r=>Number(r.net_value));let lo=Math.min(1,...values),hi=Math.max(1,...values);const pad=Math.max((hi-lo)*.15,.001);lo-=pad;hi+=pad;
  const x=i=>58+i/Math.max(rows.length-1,1)*560,y=v=>145-(v-lo)/(hi-lo)*125;
  for(let i=0;i<4;i++){const v=lo+(hi-lo)*i/3;make('line',{x1:58,y1:y(v),x2:618,y2:y(v),stroke:'#e9eff6'});make('text',{x:48,y:y(v)+4,'text-anchor':'end','font-size':11,fill:'#8a99ad'},v.toFixed(3));}
  const path=values.map((v,i)=>`${i?'L':'M'} ${x(i)} ${y(v)}`).join(' ');
  make('path',{d:`${path} L ${x(values.length-1)} 145 L 58 145 Z`,fill:'#edf4ff'});
  make('path',{d:path,fill:'none',stroke:'#397adb','stroke-width':2.3,'stroke-linejoin':'round'});
  if(values.length===1)make('circle',{cx:x(0),cy:y(values[0]),r:3,fill:'#397adb'});
  make('text',{x:58,y:174,'font-size':11,fill:'#8a99ad'},rows[0].date);
  make('text',{x:618,y:174,'text-anchor':'end','font-size':11,fill:'#8a99ad'},rows.at(-1).date);
  $('chart').replaceChildren(svg);
}
function table(container, columns, rows){
  container.replaceChildren();if(!rows.length){const p=document.createElement('p');p.className='hint';p.textContent='暂无记录';container.append(p);return;}
  const t=document.createElement('table'),head=document.createElement('thead'),tr=document.createElement('tr');
  columns.forEach(([title])=>{const th=document.createElement('th');th.textContent=title;tr.append(th);});head.append(tr);t.append(head);
  const body=document.createElement('tbody');rows.forEach(row=>{const tr=document.createElement('tr');columns.forEach(([,fn])=>{const td=document.createElement('td'),value=fn(row);if(value instanceof Node)td.append(value);else td.textContent=value??'—';tr.append(td);});body.append(tr);});t.append(body);container.append(t);
}
function detail(){
  const node=$('detail-content'),result=job?.result;
  if(activeTab==='logs'){const pre=document.createElement('pre');pre.textContent=[...(result?.logs||[]).map(x=>`${x.time}  ${x.message}`),job?.console||'',job?.error||''].filter(Boolean).join('\n')||'暂无日志';node.replaceChildren(pre);return;}
  const orderState={accepted:'待成交',filled:'已成交',rejected:'已拒绝',cancelled:'已撤销',expired:'已到期'};
  if(activeTab==='positions')table(node,[['股票',r=>r.symbol],['持仓',r=>r.quantity],['可卖',r=>r.sellable],['成本价',r=>fmt(r.average_cost,4)],['市值',r=>fmt(r.market_value)],['浮动盈亏',r=>fmt(r.unrealized_pnl)]],result?.account.positions||[]);
  if(activeTab==='orders')table(node,[['订单',r=>r.id],['股票',r=>r.symbol],['方向',r=>r.side==='buy'?'买入':'卖出'],['数量',r=>r.quantity],['状态',r=>orderState[r.status]||r.status],['提交时间',r=>r.created_at],['原因',r=>r.reason]],result?.orders||[]);
  if(activeTab==='trades')table(node,[['成交',r=>r.id],['股票',r=>r.symbol],['方向',r=>r.side==='buy'?'买入':'卖出'],['数量',r=>r.quantity],['价格',r=>fmt(r.price)],['费用',r=>fmt(Object.values(r.fees).reduce((a,b)=>a+Number(b),0))],['成交时间',r=>r.timestamp]],result?.trades||[]);
}
function render(){
  $('status').textContent=states[job.status]||job.status;$('result-name').textContent=job.name;
  const p=job.progress||{};$('progress').value=job.status==='completed'?1:Math.min(1,(p.completed||0)/(p.total||1));
  $('progress-text').textContent=p.total?`${p.stage==='loading'?'加载数据':'回放交易日'} ${p.completed}/${p.total} ${p.date||''}`:'';
  $('cancel-button').hidden=job.status!=='running';$('export-button').disabled=!job.result;
  const r=job.result;$('return-value').textContent=r?`${fmt(Number(r.metrics.total_return)*100)}%`:'—';$('drawdown-value').textContent=r?`${fmt(Number(r.metrics.max_drawdown)*100)}%`:'—';$('equity-value').textContent=r?fmt(r.account.equity):'—';$('trades-value').textContent=r?r.metrics.trade_count:'—';
  $('result-note').textContent=r?.data.note||'';
  if(r)chart(r.equity_curve);else emptyChart(job.status==='running'?'正在加载数据或回放行情…':job.error?'运行失败，请查看“日志与错误”。':'此任务没有可展示的结果。');detail();
}
async function select(id){
  selected=id;clearTimeout(timer);const current=await api(`/api/runs/${id}`);if(selected!==id)return;job=current;render();
  if(job.status==='running')timer=setTimeout(()=>select(id).catch(e=>error(e.message)),900);else await history();
}
async function history(){
  const rows=await api('/api/runs');table($('history'),[['名称',r=>r.name],['数据',r=>r.source==='demo'?'合成示例':'真实数据'],['频率',r=>r.frequency],['区间',r=>`${r.start} — ${r.end}`],['状态',r=>states[r.status]],['创建时间',r=>new Date(r.created_at).toLocaleString('zh-CN')],['操作',r=>{const b=document.createElement('button');b.className='secondary';b.textContent='查看';b.onclick=()=>select(r.id).catch(e=>error(e.message));return b;}]],rows);
}
$('run-form').onsubmit=async event=>{event.preventDefault();error('');$('run-button').disabled=true;try{const config=Object.fromEntries(fields.map(k=>[k,$(k).value]));config.strategy=$('strategy').value;config.symbols=$('symbols').value.split(/[,，\s]+/).filter(Boolean);const created=await api('/api/runs',{method:'POST',body:JSON.stringify(config)});await select(created.id);await history();}catch(e){error(e.message);}finally{$('run-button').disabled=false;}};
$('cancel-button').onclick=async()=>{try{await api(`/api/runs/${selected}/cancel`,{method:'POST'});await select(selected);}catch(e){error(e.message);}};
$('export-button').onclick=()=>{if(!job?.result)return;const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(job,null,2)],{type:'application/json'}));a.download=`quantstone-${job.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};
$('refresh-button').onclick=()=>history().catch(e=>error(e.message));
$('example-button').onclick=async()=>{if($('strategy').value&&!confirm('载入示例将替换编辑器中的当前草稿，是否继续？'))return;try{$('strategy').value=(await api('/api/example')).strategy;save();}catch(e){error(e.message);}};
function save(){try{localStorage.setItem('quantstone.strategy',$('strategy').value);$('save-state').textContent='草稿已保存到此浏览器';}catch{$('save-state').textContent='浏览器未允许保存草稿';}}
$('strategy').oninput=save;$('strategy').onkeydown=e=>{if(e.key==='Tab'){e.preventDefault();const t=e.target;t.setRangeText('    ',t.selectionStart,t.selectionEnd,'end');save();}};
$('source').onchange=()=>{$('source-note').textContent=$('source').value==='demo'?'示例是合成数据，用于验证流程。':'读取本机数据服务；公司行动区间暂不支持。';};
$('help-button').onclick=()=>{$('help').open=!$('help').open;if($('help').open)$('help').scrollIntoView({behavior:'smooth',block:'center'});};
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{activeTab=b.dataset.tab;document.querySelectorAll('[data-tab]').forEach(x=>x.setAttribute('aria-selected',String(x===b)));detail();});
(async()=>{try{let saved=null;try{saved=localStorage.getItem('quantstone.strategy');}catch{}$('strategy').value=saved??(await api('/api/example')).strategy;await history();}catch(e){error(e.message);}})();
