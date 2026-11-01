/* Shared, read-only presentation of retained observations. No retrieval or new calculations. */
(function(global){
'use strict';
const list=value=>Array.isArray(value)?value.filter(Boolean):[];
const present=value=>value!==undefined&&value!==null&&value!=='';
const aliases={ths_revenue_stock:'营业收入',ths_np_atoopc_stock:'归母净利润',ths_ncf_from_oa_stock:'经营现金流',ths_pe_ttm_stock:'PE(TTM)',close:'收盘价'};
const sameTask=(record,task)=>(!record.task_id||record.task_id===task.id)&&(record.research_version==null||record.research_version===task.version);
const scope=(items,task)=>list(items).filter(item=>sameTask(item,task));
const paper=task=>task.draft_report&&sameTask(task.draft_report,task)?task.draft_report:null;
function draftDataRefs(logic){
 const refs=[...list(logic?.data_refs),...list(logic?.mechanism?.steps).flatMap(step=>list(step?.data_refs))];
 return [...new Set(refs.filter(ref=>typeof ref==='string'&&ref))];
}
function draftFacts(task){
 const available=new Set(records(task).map(record=>record.id));
 return scope(paper(task)?.facts,task).filter(fact=>!fact.artifact_id||available.has(fact.artifact_id));
}
function knowledgeEntries(task,logic){
 const draft=paper(task),context=draft?.knowledge_context?.binding,knowledge=task.knowledge||{};
 if(!context||context.task_id!==task.id||context.research_version!==task.version||context.input_hash!==task.input_hash||typeof context.knowledge_hash!=='string')return [];
 const refs=list(logic.knowledge_refs).filter(ref=>typeof ref==='object'&&typeof ref.id==='string'&&Number.isInteger(ref.version));
 return list(knowledge.entries).filter(entry=>refs.some(ref=>ref.id===entry.id&&ref.version===entry.version)&&knowledge.index?.[entry.id]===entry.version);
}
function summary(task){
 const draft=paper(task),items=[...observations(draftFacts(task)),...observations(scope(task.evidence,task),'evidence')];
 return {observations:items,sources:records(task).length,logics:scope(draft?.logics,task).length+scope(task.theses,task).length,answers:scope(task.questions,task).filter(q=>q.status==='已回答'&&q.answer).length,conditions:scope(task.tracking,task).length};
}
function records(task){const seen=new Set();return [...list(task.materials),...list(task.source_records)].filter(record=>sameTask(record,task)&&!seen.has(record.id)&&seen.add(record.id));}
function observations(facts,kind='draft'){
 return list(facts).map((fact,index)=>({fact,kind,key:kind+':'+(fact.id||index),raw:fact.raw_value??fact.value,
  unit:Object.hasOwn(fact,'raw_unit')?fact.raw_unit:fact.unit,
  label:aliases[fact.field]||fact.label||fact.field||fact.text||'记录',object:fact.code||fact.object_label||fact.object||'',
  artifact:fact.artifact_id||fact.artifact?.id,period:String(fact.period||fact.observation_date||'期间未记录')}));
}
function numeric(item){return typeof item.raw==='number'&&Number.isFinite(item.raw)||typeof item.raw==='string'&&/^-?\d+(?:\.\d+)?(?:e[+-]?\d+)?$/i.test(item.raw);}
function unitText(unit){return !present(unit)||/待核对|待确认|未知/.test(String(unit))?'单位待核对':String(unit);}
function number(value){
 if(!present(value))return '未返回';
 const text=String(value);if(!/^-?\d+(?:\.\d+)?$/.test(text))return text;
 let [integer,fraction]=text.split('.');
 if(fraction&&fraction.length>4){
  if(/[1-9]/.test(text)&&Math.abs(Number(value))<.0001)return Number(value)===0?text:Number(value).toPrecision(4);
  const negative=integer.startsWith('-'),whole=negative?integer.slice(1):integer;
  const scaled=BigInt(whole)*10000n+BigInt(fraction.slice(0,4))+(Number(fraction[4])>=5?1n:0n);
  const rounded=scaled.toString().padStart(5,'0');
  integer=(negative?'-':'')+rounded.slice(0,-4);fraction=rounded.slice(-4);
 }
 return integer.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(fraction?'.'+fraction:'');
}
function dateKey(value){
 const match=/^(\d{4})-?(\d{2})-?(\d{2})$/.exec(value);if(!match)return value;
 const iso=match[1]+'-'+match[2]+'-'+match[3],date=new Date(iso+'T00:00:00Z');
 return Number.isFinite(date.getTime())&&date.toISOString().slice(0,10)===iso?iso:value;
}
function category(item,record){
 const field=String(item.fact.field||'');
 if(/ths_(?:revenue|np_|ncf_|gross|roe)|营业收入|净利润|经营现金流/.test(field+' '+item.label)||['income','cashflow','balancesheet'].includes(record?.operation))return ['财务对照',0];
 if(['edb_service','macro'].includes(record?.operation))return ['经济数据',2];
 if(/^(?:close|open|high|low|volume|amount|ths_pe_|ths_pb_)/.test(field)||['cmd_history_quotation','date_sequence','daily','fund_daily','daily_basic'].includes(record?.operation))return ['行情与估值',1];
 return ['研究数据',3];
}
function groups(items,task){
 const byId=new Map(records(task).map(record=>[record.id,record])),output=new Map();
 for(const item of items.filter(numeric)){
  const [name,rank]=category(item,byId.get(item.artifact));
  const object=item.fact.code||'',key=JSON.stringify([name,object,item.kind]);
  if(!output.has(key))output.set(key,{name,rank,object,kind:item.kind,items:[]});output.get(key).items.push(item);
 }
 return [...output.values()].sort((a,b)=>a.rank-b.rank);
}
function binding(task,esc){return ' data-source-task="'+esc(task.id||'')+'" data-source-version="'+esc(task.version??'')+'"';}
function cell(item,task,ctx){
 const esc=ctx.esc,raw=String(item.raw??'未返回'),display=number(item.raw),id=item.fact.id;
 const title=[item.object,item.label,item.period,'原值'+raw].filter(Boolean).join('，');
 if(!id)return '<span title="'+esc(title)+'">'+esc(display)+'</span>';
 const known=records(task).some(record=>record.id===item.artifact);
 const action=item.kind==='draft'?(known?' data-draft-source="'+esc(id)+'"':''):' data-evidence="'+esc(id)+'"';
 return action?'<button type="button" class="observation-value"'+action+binding(task,esc)+' data-observation="'+esc(item.key)+'" aria-label="查看'+esc(title)+'的依据" title="'+esc(title)+'">'+esc(display)+'</button>':'<span data-observation="'+esc(item.key)+'" title="'+esc(title)+'">'+esc(display)+'</span>';
}
function calcButton(calc,task,ctx,draft=true){
 const esc=ctx.esc,value=calc.value??calc.output?.value,unit=calc.unit??calc.output?.unit??'';
 const label=calc.label||calc.method_title||ctx.calculationName?.(calc)||calc.method||'计算结果';
 const short=label.replace('归属于母公司所有者的净利润','归母净利润').replace('原值同比变化','同比');
 const display=present(value)&&typeof calc.display==='string'&&/^-?\d+(?:\.\d+)?$/.test(calc.display)?calc.display:number(value);
 return '<button type="button" class="paper-calculation" '+(draft?'data-draft-calculation':'data-calculation')+'="'+esc(calc.id)+'"'+binding(task,esc)+' aria-label="查看'+esc(label)+'的公式与输入"><span>'+esc(short)+'</span><strong>'+esc(present(value)?display+unit:'查看计算')+'</strong><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m8 5 5 5-5 5"/></svg></button>';
}
function matrixRows(group){
 const rows=new Map();
 for(const item of group.items){
  const key=JSON.stringify([item.fact.code||item.object,item.fact.field||item.label,item.unit??'',item.fact.series_basis||'',item.kind]);
  if(!rows.has(key))rows.set(key,{label:item.label,object:item.object,unit:item.unit,items:[]});rows.get(key).items.push(item);
 }
 return rows;
}
function seriesData(group){
 const periods=[...new Set(group.items.map(item=>dateKey(item.period)))].sort(),rows=[...matrixRows(group).values()];
 // Each metric keeps its unit. Ambiguous observations remain a table.
 if(periods.length<3||periods.length>500||rows.length>4||!rows.length||periods.some(period=>!/^\d{4}-\d{2}-\d{2}$/.test(period)||!Number.isFinite(Date.parse(period))||new Date(period+'T00:00:00Z').toISOString().slice(0,10)!==period))return null;
 const series=[];
 for(const row of rows){
  const points=periods.map(period=>row.items.filter(item=>dateKey(item.period)===period));
  if(points.some(bucket=>bucket.length>1)||row.items.some(item=>!Number.isFinite(Number(item.raw))||Math.abs(Number(item.raw))>Number.MAX_SAFE_INTEGER||(Number(item.raw)===0&&/[1-9]/.test(String(item.raw).split(/[eE]/)[0]))))return null;
  series.push({label:!group.object&&row.object?row.object+' '+row.label:row.label,unit:unitText(row.unit),points:points.map(bucket=>bucket.length?{key:bucket[0].key,raw:String(bucket[0].raw),value:Number(bucket[0].raw)}:null)});
 }
 if(new Set(series.map(row=>row.label+' '+row.unit)).size!==series.length)return null;
 return {name:group.name,periods,series};
}
function grid(group,task,ctx,options={}){
 const esc=ctx.esc,rows=matrixRows(group),periods=[...new Set(group.items.map(item=>dateKey(item.period)))].sort();
 const units=[...new Set([...rows.values()].map(row=>unitText(row.unit)))],common=units.length===1?units[0]:'';
 const calculations=options.calculations||[],matches=row=>calculations.find(calc=>calc.method==='raw_change_v1'&&list(calc.data_refs).length===2&&calc.data_refs.every(id=>row.items.some(item=>item.fact.id===id)));
 const results=options.results&&[...rows.values()].some(matches),latest=periods.at(-1);
 const table='<div class="observation-scroll" tabindex="0" role="region" aria-label="'+esc(group.name)+'数据对照"><table class="observation-matrix'+(options.compact?' observation-matrix--compact':'')+'"><thead><tr><th scope="col">指标'+(common?'<span class="matrix-unit">'+esc(common)+'</span>':'')+'</th>'+periods.map(period=>'<th scope="col"'+(period===latest?' class="observation-latest"':'')+'><time>'+esc(period.replaceAll('-','.'))+'</time></th>').join('')+(results?'<th scope="col">期间变化</th>':'')+'</tr></thead><tbody>'+[...rows.values()].map(row=>'<tr><th scope="row"><span>'+esc(row.label)+'</span>'+(!common?'<span class="matrix-unit">'+esc(unitText(row.unit))+'</span>':'')+(!group.object&&row.object&&row.object!==row.label?'<span class="matrix-unit">'+esc(row.object)+'</span>':'')+'</th>'+periods.map(period=>{
  const bucket=row.items.filter(item=>dateKey(item.period)===period);
  return '<td'+(period===latest?' class="observation-latest"':'')+'>'+(bucket.length?bucket.map(item=>cell(item,task,ctx)).join(''):'<span class="observation-missing" aria-label="该期间未取得">—</span>')+'</td>';
 }).join('')+(results?'<td class="matrix-calculation">'+(matches(row)?calcButton(matches(row),task,ctx):'—')+'</td>':'')+'</tr>').join('')+'</tbody></table></div>';
 const chart=seriesData(group);if(!chart)return table;
 const identity=JSON.stringify([task.id,task.version,group.kind,group.object,group.items.map(item=>item.key)]);
 return '<div class="observation-view" data-evidence-key="'+esc(identity)+'" data-evidence-series="'+esc(JSON.stringify(chart))+'" data-presentation="table"><div class="observation-tools" role="group" aria-label="'+esc(group.name)+'呈现方式"><button type="button" class="observation-mode" data-evidence-view="table" aria-pressed="true">表格</button><button type="button" class="observation-mode" data-evidence-view="chart" aria-pressed="false">趋势</button></div><div class="observation-table">'+table+'</div><div class="observation-chart" hidden role="img" aria-label="'+esc(group.name)+'；各指标单独绘制，缺失时点留空。逐项数据与原件可切回表格查看。"></div><p class="observation-chart-error" role="status" hidden></p></div>';

}
function sourceNotes(items,task,ctx){
 const esc=ctx.esc,byId=new Map(records(task).map(record=>[record.id,record])),ids=[...new Set(items.map(item=>item.artifact).filter(Boolean))];
 const names=[...new Set(ids.map(id=>byId.get(id)).filter(Boolean).map(record=>ctx.sourceRecordName(record)))];
 return names.length?'<div class="source-citations"><span>来源</span>'+names.map(name=>'<span>'+esc(name)+'</span>').join('')+'</div>':'';
}
function library(task,items,ctx){
 const esc=ctx.esc,available=records(task);if(!available.length)return '';
 return '<details class="source-library desk-disclosure"><summary>原始资料<span>'+available.length+'份</span></summary><div class="source-library-list">'+available.map(record=>{
  const excluded=list(task.draft_source_issues).some(issue=>issue.artifact_id===record.id);
  const used=items.filter(item=>item.artifact===record.id),dates=[...new Set(used.map(item=>dateKey(item.period)))].sort();
  const period=dates.length?dates.length===1?dates[0]:dates[0]+' 至 '+dates.at(-1):'';
  const labels=[...new Set(used.map(item=>item.label))];
  return '<div class="source-library-row"><div><button type="button" class="source-citation" data-source="'+esc(record.id)+'"'+binding(task,esc)+'>'+esc(ctx.sourceRecordName(record))+'<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m8 5 5 5-5 5"/></svg></button>'+(labels.length?'<p>'+esc(labels.join('、'))+'</p>':'')+'</div><div class="source-library-period">'+(period?'<time>'+esc(period)+'</time>':'')+'<span>'+(excluded?'未用于报告':used.length?used.length+'条关联记录':record.operation==='get_limit_policy'?'接口检查记录':'未关联研究数值')+'</span></div></div>';
 }).join('')+'</div></details>';
}
global.RHResearchData=Object.freeze({list,scope,paper,draftDataRefs,draftFacts,knowledgeEntries,summary,records,observations,numeric,groups,grid,cell,number,seriesData,calcButton,sourceNotes,library,unitText});
})(window);
