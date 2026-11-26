/* Shared, read-only presentation of retained observations. No retrieval or new calculations. */
(function(global){
'use strict';
const list=value=>Array.isArray(value)?value.filter(Boolean):[];
const present=value=>value!==undefined&&value!==null&&value!=='';
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const aliases={ths_revenue_stock:'营业收入',ths_np_atoopc_stock:'归母净利润',ths_ncf_from_oa_stock:'经营现金流',ths_pe_ttm_stock:'PE(TTM)',close:'收盘价',latest:'最新价',minute_close:'分钟收盘价',volume:'成交量',amount:'成交额'};
const financialFields=new Set(['ths_revenue_stock','ths_operating_cost_stock','ths_np_stock','ths_np_atoopc_stock','ths_ncf_from_oa_stock','ths_account_receivable_stock','ths_inventory_stock','ths_accounts_payable_stock']);
const missingCells=new Set([null,'','-','--','N/A','null']);
const providerNames={ifind:'iFinD',tinyshare:'Tinyshare',wind:'Wind'};
const providerName=record=>Object.hasOwn(providerNames,record.source)?providerNames[record.source]:'';
function observationUnit(fact){
 const unit=Object.hasOwn(fact,'raw_unit')?fact.raw_unit:fact.unit,ref=fact.unit_reference;
 return unit==='元'&&fact.currency==='RMB'&&fact.source==='ifind'&&ref?.kind==='registered_field'&&ref.registry==='ifind.cmd_history_quotation'&&ref.family==='price'&&ref.field===fact.field&&ref.currency_parameter==='RMB'?'人民币元':unit;
}
const sameTask=(record,task)=>(!record.task_id||record.task_id===task.id)&&(record.research_version==null||record.research_version===task.version);
const scope=(items,task)=>list(items).filter(item=>sameTask(item,task));
const paper=task=>task.draft_report&&sameTask(task.draft_report,task)?task.draft_report:null;
// Old workspaces stored a delivery note as a data gap. Leave the archive intact.
const researchGaps=task=>list(task?.gaps).filter(item=>typeof item==='string'&&item.trim()&&
 item.trim()!=='研究草稿已生成，独立核验可在后续版本补充。');
function reportCheckpoint(task){
 if(!['失败','已取消'].includes(task?.status)||task.batch_settling===true||
    (task.delivery&&task.delivery.version===task.version))return null;
 if(task.has_pending_content??(task.pending_content||task.pending_html))return 'content';
 return task.has_retained_submission===true?'submission':null;
}
function taskStatus(task){
 const status=typeof task?.status==='string'?task.status:'';
 const processLabels={'待开始':'研究待开始','运行中':'研究正在运行','已完成':'研究已完成',
  '部分完成':'研究部分完成','失败':'研究未完成','已取消':'研究已取消'};
 const tone=['成功','已完成','支持','已保存'].includes(status)?'verified':
  ['失败','已取消','反驳','部分完成'].includes(status)?'attention':status==='运行中'?'running':'neutral';
 const result={status,label:status||'状态待确认',processLabel:Object.hasOwn(processLabels,status)?processLabels[status]:status||'研究状态待确认',tone,title:''};
 if(task?.batch_settling===true)
  return {...result,label:status==='已取消'?'正在停止':'正在收尾',
   processLabel:status==='已取消'?'正在停止研究':'研究正在收尾',tone:'running'};
 const checkpoint=reportCheckpoint(task);
 if(checkpoint){
  const label=checkpoint==='content'?'报告待保存':'研究原文已保留';
  return {...result,label,processLabel:label};
 }
 const delivery=task?.delivery,hash=value=>typeof value==='string'&&/^[0-9a-f]{64}$/.test(value);
 const savedDraft=['部分完成','已完成'].includes(status)&&typeof task.id==='string'&&task.id.length>0&&
  Number.isSafeInteger(task.version)&&task.version>0&&delivery&&typeof delivery==='object'&&!Array.isArray(delivery)&&
  delivery.kind==='research_draft'&&delivery.version===task.version&&hash(delivery.digest)&&hash(delivery.html_sha256)&&
  (!Object.hasOwn(delivery,'task_id')||delivery.task_id===task.id);
 if(savedDraft){
  const gaps=list(paper(task)?.readout_gaps).filter(item=>typeof item==='string'&&item.trim());
  return {...result,label:'草稿已保存',
   processLabel:gaps.length?'草稿已保存，'+(gaps.length===1?gaps[0]:gaps.length+'项待核对'):'草稿已保存',
   tone:gaps.length?'attention':'neutral',
   title:gaps.length?gaps.join('；'):status==='部分完成'?'研究部分完成，草稿已保存':'研究已完成，交付内容为草稿'};
 }
 return result;
}
function draftDataRefs(logic){
 const refs=[...list(logic?.data_refs),...list(logic?.mechanism?.steps).flatMap(step=>list(step?.data_refs))];
 return [...new Set(refs.filter(ref=>typeof ref==='string'&&ref))];
}
function draftFacts(task){
 const available=new Set(records(task).map(record=>record.id));
 return scope(paper(task)?.facts,task).filter(fact=>!fact.artifact_id||available.has(fact.artifact_id));
}
function draftEvidence(task,logic){
 const excluded=new Set(list(logic.off_scope_data_refs));
 const requestedCalculations=list(logic.calculation_refs);
 const submitted=scope(paper(task)?.calculations,task).filter(item=>requestedCalculations.includes(item.id));
 const calculations=submitted.filter(item=>!list(item.data_refs).some(ref=>excluded.has(ref)));
 const refs=new Set(draftDataRefs(logic).filter(ref=>!excluded.has(ref)));
 calculations.forEach(item=>list(item.data_refs).forEach(ref=>refs.add(ref)));
 const facts=draftFacts(task).filter(row=>refs.has(row.id));
 const directRefs=[...new Set(list(logic.source_refs).filter(ref=>typeof ref==='string'&&ref))];
 const sourceRefs=new Set([...directRefs,...facts.map(row=>row.artifact_id)]);
 const sources=records(task).filter(record=>sourceRefs.has(record.id));
 const availableFacts=new Set(facts.map(row=>row.id));
 const availableCalculations=new Set(submitted.map(row=>row.id));
 const availableSources=new Set(sources.map(row=>row.id));
 const missing=[...refs].filter(ref=>!availableFacts.has(ref))
  .concat(requestedCalculations.filter(ref=>!availableCalculations.has(ref)),directRefs.filter(ref=>!availableSources.has(ref)));
 return {facts,calculations,sources,missing};
}
function draftExcerpts(logic, sources){
 return list(logic.document_excerpts).flatMap(location=>{
  const record=sources.find(item=>item.id===location.source_ref);
  if(!record||location.snapshot_hash!==(record.snapshot_hash||record.hash))return [];
  const material=record.kind==='filing'?record.snapshot?.material:record.snapshot;
  const part=material?.parts?.[location.part_index],{start,end}=location;
  if(typeof part?.text!=='string'||typeof part.locator!=='string'||!Number.isInteger(start)
      ||!Number.isInteger(end)||start<0||end<=start||end>part.text.length)return [];
  // Server offsets count Unicode code points. Array.from preserves the same
  // address when a retained original contains an astral character.
  const characters=Array.from(part.text);
  if(end>characters.length)return [];
  const quote=characters.slice(start,end).join('');
  return quote?[{record,part:part.locator,quote}]:[];
 });
}
function draftExcerptHTML({record,part,quote},task,{esc,sourceRecordName}){
 const label=sourceRecordName(record)+'，'+part;
 return '<div class="paper-excerpt"><p><q>'+esc(quote)+'</q></p><button type="button" class="source-citation"'+
  ' aria-haspopup="dialog" data-source="'+esc(record.id)+'" data-draft-excerpt="'+esc(JSON.stringify({part,quote}))+'"'+
  binding(task,esc)+'>'+esc(label)+'</button></div>';
}
function knowledgeEntries(task,logic){
 const draft=paper(task),context=draft?.knowledge_context?.binding,knowledge=task.knowledge||{};
 if(!context||context.task_id!==task.id||context.research_version!==task.version||context.input_hash!==task.input_hash||typeof context.knowledge_hash!=='string')return [];
 const refs=list(logic.knowledge_refs).filter(ref=>typeof ref==='object'&&typeof ref.id==='string'&&Number.isInteger(ref.version));
 return list(knowledge.entries).filter(entry=>refs.some(ref=>ref.id===entry.id&&ref.version===entry.version)&&knowledge.index?.[entry.id]===entry.version);
}
function reportConditions(draft){
 if(!draft||!Array.isArray(draft.logics))return [];
 return draft.logics.flatMap((logic,index)=>{
  if(!logic||typeof logic.title!=='string')return [];
  const premise=typeof logic.argument?.condition==='string'?logic.argument.condition.trim():'';
  const proposed=draft.tracking_version===1?logic.tracking:null;
  const rule=validTrackingRule(proposed)?proposed:null;
  return premise||rule?[{logic_index:index+1,title:logic.title,condition:premise,tracking:rule}]:[];
 });
}
function validTrackingRule(rule){
 const required=['indicator','source','window','condition'];
 return !!rule&&typeof rule==='object'&&!Array.isArray(rule)&&
  required.every(key=>typeof rule[key]==='string'&&rule[key].trim())&&
  Object.keys(rule).every(key=>[...required,'benchmark'].includes(key)&&typeof rule[key]==='string'&&rule[key].trim());
}
function summary(task){
 const draft=paper(task),items=[...observations(draftFacts(task)),...observations(scope(task.evidence,task),'evidence')];
 return {observations:items,sources:records(task).length,logics:scope(draft?.logics,task).length+scope(task.theses,task).length,answers:scope(task.questions,task).filter(q=>q.status==='已回答'&&q.answer).length,conditions:scope(task.tracking,task).length+reportConditions(draft).length};
}
function records(task){
 if(!task)return [];
 const available=new Map(),specificity=record=>Number(Boolean(record.task_id))+Number(record.research_version!=null);
 for(const record of scope([...list(task.materials),...list(task.source_records)],task)){
  if(typeof record.id!=='string'||!record.id)continue;
  const prior=available.get(record.id);
  if(!prior||specificity(record)>specificity(prior))available.set(record.id,record);
 }
 return [...available.values()];
}
function observations(facts,kind='draft'){
 return list(facts).map((fact,index)=>({fact,kind,key:kind+':'+(fact.id||index),raw:Object.hasOwn(fact,'raw_value')?fact.raw_value:fact.value,
  unit:observationUnit(fact),
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
function calendarDay(value){
 if(typeof value!=='string'||!/^(?:\d{8}|\d{4}([-\/])\d{2}\1\d{2})$/.test(value)||value.startsWith('0000'))return '';
 const iso=value.length===10?value.replaceAll('/','-'):value.slice(0,4)+'-'+value.slice(4,6)+'-'+value.slice(6,8);
 const date=new Date(iso+'T00:00:00Z');
 return Number.isFinite(date.getTime())&&date.toISOString().slice(0,10)===iso?iso:'';
}
const dateKey=value=>calendarDay(value)||value.replace(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})\+08:00$/,'$1 $2');
// The label describes the request, not approved financial applicability.
function requestedStatement(item,record){
 const fact=item.fact,params=record?.parameters;
 if(record?.source!=='ifind'||record.operation!=='basic_data_service'||
    !financialFields.has(fact.field)||fact.period_basis!=='请求D报告期'||
    typeof params?.codes!=='string'||!params.codes.split(',').map(code=>code.trim()).includes(fact.code)||
    !Array.isArray(params.indipara))return '';
 const matches=params.indipara.filter(row=>row?.indicator===fact.field);
 if(matches.length!==1||!Array.isArray(matches[0].indiparams)||matches[0].indiparams.length!==2)return '';
 const [period,type]=matches[0].indiparams,labels={'1':'合并报表','2':'母公司报表','3':'合并调整报表','4':'母公司调整报表'};
 if(typeof period!=='string'||typeof type!=='string'||!Object.hasOwn(labels,type))return '';
 const requested=calendarDay(period);
 if(!requested||requested!==calendarDay(item.period))return '';
 return labels[type];
}
// Group only by the original field request, with its query window removed.
// Incomplete legacy requests remain identifiable by their own original.
function marketRequest(item,record){
 if(record?.source!=='ifind'||!['cmd_history_quotation','date_sequence'].includes(record.operation))return null;
 const unbound={key:JSON.stringify(['original',record.id]),caption:'请求口径未明确'};
 const params=record.parameters,settings=params?.functionpara,field=item.fact.field;
 if(!object(params)||!object(settings)||typeof params.codes!=='string'||
    params.codes.split(',').filter(code=>code===item.fact.code).length!==1)return unbound;
 const start=calendarDay(params.startdate),end=calendarDay(params.enddate),period=calendarDay(item.period);
 if(!start||!end||!period||period<start||period>end)return unbound;
 const quotes=record.operation==='cmd_history_quotation';
 const selector=quotes?'indicators':'indipara';
 let binding=null;
 if(quotes){
  if(typeof params.indicators!=='string'||params.indicators.split(',').filter(name=>name===field).length!==1)return unbound;
 }else{
  const matches=list(params.indipara).filter(entry=>entry?.indicator===field);
  if(matches.length!==1||!Array.isArray(matches[0].indiparams))return unbound;
  binding=matches[0];
 }
 const omitted=['codes','startdate','enddate',selector];
 const retained=Object.fromEntries(Object.entries(params).filter(([key])=>!omitted.includes(key)));
 const key=JSON.stringify([record.operation,binding,retained],(_,value)=>object(value)?
  Object.fromEntries(Object.keys(value).sort().map(name=>[name,value[name]])):value);
 const caption=quotes&&settings.CPS==='1'?'请求未复权':settings.Fill==='Blank'?'请求缺值留空':'查询口径';
 return {key,caption};
}
const dataCategories=Object.freeze([
 ['market','行情'],['financial','财务'],['valuation','估值'],['fund','基金与指数'],
 ['industry','行业'],['macro','宏观'],['documents','公告与材料'],
]);
function dataCategory(fact,record){
 const field=String(fact?.field||fact?.record_locator?.field||'').toLowerCase(),operation=record?.operation;
 if(/^(?:pe(?:_|$)|pb(?:_|$)|ps(?:_|$)|ths_(?:pe|pb|ps)_)/.test(field)||field==='market_cap')return 'valuation';
 if(financialFields.has(field)||/^ths_(?:revenue|np_|ncf_|gross|roe|total_assets|total_liab|operating_cost)/.test(field))return 'financial';
 if(/^(?:weight|unit_nav|nav|ths_unit_nvnf_fund|fund_share|management_fee|custody_fee|etf_exchange_nav)$/.test(field)
    ||['index_file','index_profile','index_catalog','etf_pcf','etf_nav_history','fund_nav','index_weight'].includes(operation))return 'fund';
 if(/^(?:close|latest|minute_close|open|high|low|volume|vol|amount|pct_chg|turnover_rate)$/.test(field))return 'market';
 if(fact?.economic_context||['edb_service','search_edb','macro','nbs_series'].includes(operation))return 'macro';
 if(['income','cashflow','balancesheet'].includes(operation))return 'financial';
 if(['smart_stock_picking','industry_classification','index_member'].includes(operation))return 'industry';
 if(['cmd_history_quotation','real_time_quotation','high_frequency','daily','fund_daily','daily_basic','etf_daily_quotes',
    'hsi_close_history','mainland_index_close_history'].includes(operation))return 'market';
 return 'documents';
}
function requestedFields(record){
 const params=record?.parameters||{};
 return [...new Set([...(typeof params.indicators==='string'?params.indicators.split(','):[]),
  ...list(params.indipara).map(item=>item.indicator),...(typeof params.fields==='string'?params.fields.split(','):[])]
  .filter(field=>typeof field==='string'&&field.trim()).map(field=>field.trim()))];
}
function categoryNeeds(task,items){
 const output=new Map(),available=records(task);
 for(const need of scope(task.data_need_specs,task)){
  if(!need.id||diagnosticOperations.has(need.operation))continue;
  const related=available.filter(record=>list(record.data_need_ids).includes(need.id));
  const wanted=need.operation==='high_frequency'?['minute_close']:requestedFields(need);
  const fields=wanted.length?wanted:[''];
  const codes=typeof need.parameters?.codes==='string'?need.parameters.codes.split(',').map(value=>value.trim()).filter(Boolean):[];
  for(const field of fields){
   const category=dataCategory({field},need);
   const matching=items.filter(item=>item.kind!=='pending'&&related.some(record=>record.id===item.artifact)
    &&(!field||String(item.fact.field||item.fact.record_locator?.field).toLowerCase()===field.toLowerCase()
      ||need.operation==='edb_service'&&item.fact.code===field&&item.fact.field==='value'));
   const sources=related.filter(record=>!['失败','请求失败','接口失败','成功但为空'].includes(record.status));
   for(const subject of codes.length?codes:['']){
    if(matching.some(item=>(!subject||[item.fact.code,item.object].includes(subject))&&(numeric(item)||Boolean(item.fact.text)))
       ||(!field&&sources.some(record=>record.kind!=='api')))continue;
    const note=related.length?'未取得可用读数':'待取得';
    const label=aliases[field]||need.title||need.label||field||'资料';
    if(!output.has(category))output.set(category,new Map());
    output.get(category).set(JSON.stringify([label,subject]),{label,subject,note});
   }
  }
 }
 return output;
}
function groups(items,task){
 const byId=new Map(records(task).map(record=>[record.id,record])),output=new Map();
 for(const item of items){
  const record=byId.get(item.artifact);
  const composition=item.fact.axis_role==='cross_section'&&item.fact.field==='weight'&&record?.source==='official'&&record.operation==='index_file';
  const missing=record&&item.kind==='draft'&&item.fact.axis_role==='time'&&calendarDay(item.period)&&
   Object.hasOwn(item.fact,'raw_value')&&missingCells.has(item.raw);
  if(!numeric(item)&&!composition&&!missing)continue;
  const category=dataCategory(item.fact,record);
  const rank=dataCategories.findIndex(([key])=>key===category),label=dataCategories[rank][1];
  const name=composition?'指数成分':category==='financial'?'财务对照':category==='fund'&&['unit_nav','ths_unit_nvnf_fund'].includes(item.fact.field)?'基金净值':label;
  const object=composition?record.snapshot?.parsed?.index_code||record.snapshot?.index_code||'':item.fact.code||'';
  const key=JSON.stringify([category,name,object,item.kind,composition?record.id:'']);
  if(!output.has(key))output.set(key,{category,name,rank,object,kind:item.kind,composition,items:[]});
  output.get(key).items.push({...item,requestStatement:requestedStatement(item,record),market:marketRequest(item,record)});
 }
 return [...output.values()].sort((a,b)=>a.rank-b.rank);
}
function binding(task,esc){return ' data-source-task="'+esc(task.id||'')+'" data-source-version="'+esc(task.version??'')+'"';}
function cell(item,task,ctx){
 const esc=ctx.esc,raw=String(item.raw??'未返回'),display=ctx.rawValues&&present(item.raw)?raw:number(item.raw),id=item.fact.id;
 const title=[item.object,item.label,item.requestStatement?'请求'+item.requestStatement:'',item.period,'原值'+raw].filter(Boolean).join('，');
 if(!id)return '<span title="'+esc(title)+'">'+esc(display)+'</span>';
 const known=records(task).some(record=>record.id===item.artifact);
 const action=item.kind==='draft'?(known?' data-draft-source="'+esc(id)+'"':''):' data-evidence="'+esc(id)+'"';
 return action?'<button type="button" class="observation-value"'+action+binding(task,esc)+' data-observation="'+esc(item.key)+'" aria-label="查看'+esc(title)+'的依据" title="'+esc(title)+'">'+esc(display)+'</button>':'<span data-observation="'+esc(item.key)+'" title="'+esc(title)+'">'+esc(display)+'</span>';
}
function calcButton(calc,task,ctx,draft=true){
 const esc=ctx.esc,value=calc.value??calc.output?.value,unit=calc.unit??calc.output?.unit??'';
 const label=calc.label||calc.method_title||ctx.calculationName?.(calc)||calc.method||'计算结果';
 const short=label.replace('归属于母公司所有者的净利润','归母净利润').replace('原值同比变化','同比');
 const display=present(value)&&typeof calc.display==='string'&&/^-?\d+(?:\.\d+)?(?:e[+-]?\d+)?$/i.test(calc.display)&&Number.isFinite(Number(calc.display))?calc.display:number(value);
 return '<button type="button" class="paper-calculation" '+(draft?'data-draft-calculation':'data-calculation')+'="'+esc(calc.id)+'"'+binding(task,esc)+' aria-label="查看'+esc(label)+'的公式与输入"><span>'+esc(short)+'</span><strong>'+esc(present(value)?display+unit:'查看计算')+'</strong><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m8 5 5 5-5 5"/></svg></button>';
}
function matrixRows(group){
 const rows=new Map(),families=new Map(),financialContexts=new Map();
 for(const item of group.items){
  const object=item.fact.code||item.object,requestStatement=item.requestStatement||'';
  const key=JSON.stringify([object,item.fact.field||item.label,item.unit??'',item.fact.series_basis||'',item.market?.key||'',item.kind,requestStatement]);
  if(!rows.has(key)){
   const row={label:item.label,object:item.object,unit:item.unit,requestStatement,market:item.market,items:[]};rows.set(key,row);
   const family=JSON.stringify([object,item.label,item.unit??'']);
   if(!families.has(family))families.set(family,[]);families.get(family).push(row);
   if(financialFields.has(item.fact.field)){
    const context=JSON.stringify([object,item.kind]);
    if(!financialContexts.has(context))financialContexts.set(context,[]);
    financialContexts.get(context).push(row);
   }
  }
  rows.get(key).items.push(item);
 }
 // A single comparable row needs no extra explanation. Distinct requests must
 // remain distinguishable even when a legacy fact has no series fingerprint.
 const nameScopes=items=>{for(const row of items)row.scopeLabel=row.requestStatement?'请求'+row.requestStatement:'请求口径未明确';};
 for(const family of families.values()){
  if(family.length<2)continue;
  nameScopes(family);
  const marketRows=family.filter(row=>row.market);
  const counts=new Map(),positions=new Map();
  for(const row of marketRows)counts.set(row.market.caption,(counts.get(row.market.caption)||0)+1);
  for(const row of marketRows){
   const caption=row.market.caption,position=(positions.get(caption)||0)+1;
   positions.set(caption,position);
   row.scopeLabel=counts.get(caption)===1?caption:caption+'，查询'+position;
  }
 }
 // Unit and metric differences do not explain whether a row was requested
 // from a consolidated or parent statement. Name mixed contexts per object.
 for(const context of financialContexts.values())if(new Set(context.map(row=>row.requestStatement)).size>1)nameScopes(context);
 return rows;
}
function seriesData(group){
 const periods=[...new Set(group.items.map(item=>dateKey(item.period)))].sort();
 const rows=[...matrixRows(group).values()].filter(row=>row.items.some(numeric));
 // Each metric keeps its unit. Ambiguous observations remain a table.
 if(periods.length<3||periods.length>500||rows.length>4||!rows.length||periods.some(period=>!calendarDay(period)))return null;
 const series=[];
 for(const row of rows){
  const points=periods.map(period=>row.items.filter(item=>dateKey(item.period)===period));
  if(points.some(bucket=>bucket.length>1)||row.items.filter(numeric).some(item=>!Number.isFinite(Number(item.raw))||Math.abs(Number(item.raw))>Number.MAX_SAFE_INTEGER||(Number(item.raw)===0&&/[1-9]/.test(String(item.raw).split(/[eE]/)[0]))))return null;
  const label=(!group.object&&row.object?row.object+' '+row.label:row.label)+(row.scopeLabel?'，'+row.scopeLabel:'');
  series.push({label,unit:unitText(row.unit),points:points.map(([item])=>item&&numeric(item)?{key:item.key,raw:String(item.raw),value:Number(item.raw)}:null)});
 }
 if(new Set(series.map(row=>row.label+' '+row.unit)).size!==series.length)return null;
 return {name:group.name,periods,series};
}
function compositionGrid(group,task,ctx){
 const esc=ctx.esc;
 const units=[...new Set(group.items.map(item=>unitText(item.unit)))],unit=units.length===1?units[0]:'';
 return '<div class="observation-scroll observation-scroll--composition" tabindex="0" role="region" aria-label="'+esc(group.object+'指数成分权重')+'"><table class="observation-matrix observation-matrix--composition"><thead><tr><th scope="col">成分</th><th scope="col">代码</th><th scope="col">权重'+(unit?'<span class="matrix-unit">'+esc(unit)+'</span>':'')+'</th><th scope="col">观测日期</th></tr></thead><tbody>'+group.items.map(item=>'<tr><th scope="row">'+esc(item.fact.object_label||item.fact.code||item.object)+'</th><td>'+esc(item.fact.code||item.object)+'</td><td>'+cell(item,task,ctx)+(!unit?'<span class="matrix-unit">'+esc(unitText(item.unit))+'</span>':'')+'</td><td><time>'+esc(dateKey(item.period))+'</time></td></tr>').join('')+'</tbody></table></div>';
}
function grid(group,task,ctx,options={}){
 if(group.composition)return compositionGrid(group,task,ctx);
 const esc=ctx.esc,rows=matrixRows(group),periods=[...new Set(group.items.map(item=>dateKey(item.period)))].sort();
 const units=[...new Set([...rows.values()].map(row=>unitText(row.unit)))],common=units.length===1?units[0]:'';
 const calculations=options.calculations||[],matches=row=>calculations.find(calc=>calc.method==='raw_change_v1'&&list(calc.data_refs).length===2&&calc.data_refs.every(id=>row.items.some(item=>item.fact.id===id)));
 const results=options.results&&[...rows.values()].some(matches),latest=periods.at(-1);
 const table='<div class="observation-scroll" tabindex="0" role="region" aria-label="'+esc(group.name)+'数据对照"><table class="observation-matrix'+(options.compact?' observation-matrix--compact':'')+'"><thead><tr><th scope="col">指标'+(common?'<span class="matrix-unit">'+esc(common)+'</span>':'')+'</th>'+periods.map(period=>'<th scope="col"'+(period===latest?' class="observation-latest"':'')+'><time>'+esc(period.replaceAll('-','.'))+'</time></th>').join('')+(results?'<th scope="col">期间变化</th>':'')+'</tr></thead><tbody>'+[...rows.values()].map(row=>'<tr><th scope="row"><span>'+esc(row.label)+'</span>'+(row.scopeLabel?'<span class="matrix-unit">'+esc(row.scopeLabel)+'</span>':'')+(!common?'<span class="matrix-unit">'+esc(unitText(row.unit))+'</span>':'')+(!group.object&&row.object&&row.object!==row.label?'<span class="matrix-unit">'+esc(row.object)+'</span>':'')+'</th>'+periods.map(period=>{
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
function recentReadings(group,task,ctx){
 const {esc}=ctx;
 if(group.composition)return '<p class="source-reading-range">'+group.items.length+'只成分</p>';
 const readings=[];
 for(const row of matrixRows(group).values()){
  const points=row.items.filter(numeric);
  const last=points.map(item=>dateKey(item.period)).sort().at(-1);
  const mostRecent=points.filter(item=>dateKey(item.period)===last);
  if(mostRecent.length!==1)continue;
  const item=mostRecent[0];
  const periods=points.map(point=>dateKey(point.period)).sort();
  readings.push('<li><div><strong>'+esc(row.label)+'</strong>'+(row.scopeLabel?'<span>'+esc(row.scopeLabel)+'</span>':'')
   +'</div><div class="source-reading-value">'+cell(item,task,ctx)+'<span>'+esc(unitText(item.unit))+'</span></div><time>'
   +esc(periods[0]===last?last:periods[0]+' – '+last)+'</time></li>');
 }
 return readings.length?'<ul class="source-readings">'+readings.join('')+'</ul>':'';
}
function sourceCitation(record,task,ctx,item){
 if(!record)return '';
 const {esc}=ctx,id=item?.fact.id;
 const action=id&&item.kind==='draft'?' data-draft-source="'+esc(id)+'"':
  ' data-source="'+esc(record.id)+'"'+(id?' data-source-evidence="'+esc(id)+'"':'');
 return '<button type="button" class="source-citation" aria-haspopup="dialog"'+action+binding(task,esc)+'><span>'+
  esc(ctx.sourceRecordName(record))+'</span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m8 5 5 5-5 5"/></svg></button>';
}
function navigationRecord(record){
 // These records locate objects and data; absence of numeric cells is expected.
 if(record.kind!=='api'||!['成功待核验','成功但为空','已解析'].includes(record.status))return false;
 if(record.source==='official')return ['release_catalog','index_catalog'].includes(record.operation);
 if(record.source!=='ifind')return false;
 if(['get_limit_policy','get_thscode','get_indicators','get_indicator_params','search_edb','get_trade_dates','report_query'].includes(record.operation))return true;
 const params=record.parameters;
 return record.operation==='smart_stock_picking'&&params&&Object.keys(params).length===2&&params.searchtype==='stock'&&
  typeof params.searchstring==='string'&&/^[A-Za-z0-9\u4e00-\u9fff]{1,24}(行业|产业链)股票$/.test(params.searchstring);
}
const diagnosticOperations=new Set(['get_limit_policy','get_thscode','get_trade_dates','get_indicators','get_indicator_params','search_edb']);
function libraryEntry(entry,task,esc){
 const grouped=entry.rows.length>1;
 const objects=grouped&&entry.rows.every(row=>row.objects===entry.rows[0].objects)?entry.rows[0].objects:'';
 const heading=grouped?'<div class="source-library-group"><span class="source-library-label"><strong>'+esc(entry.title)+'</strong>'+
  (entry.fields?'<span>'+esc(entry.fields)+'</span>':'')+'</span>'+(objects?'<span class="source-library-code">'+esc(objects)+'</span>':'')+'</div>':'';
 const scopes=entry.rows.map(row=>JSON.stringify([row.objects,row.period,row.requestWindow,row.detail]));
 const totals=new Map();
 for(const scope of scopes)totals.set(scope,(totals.get(scope)||0)+1);
 return '<div class="source-library-entry'+(grouped?' source-library-entry--grouped':'')+'">'+heading+
  '<ul'+(grouped?' aria-label="'+esc(entry.title)+'"':'')+'>'+entry.rows.map((row,index)=>{
  const sequence=totals.get(scopes[index])>1?'请求'+(index+1):'';
  const coverage=row.period||row.requestWindow,day=calendarDay(coverage);
  const date=day?'<time datetime="'+esc(day)+'">'+esc(coverage)+'</time>':esc(coverage);
  const subject=objects?'':row.objects;
  const name=grouped?(date||esc(subject||sequence||'原件'+(index+1))):esc(entry.title);
  const label=[row.name,entry.fields,row.objects,coverage,sequence,row.note].filter(Boolean).join('，');
  return '<li class="source-library-row"><button type="button" class="source-library-open" aria-haspopup="dialog" aria-label="'+
   esc(label)+'"'+(row.note!==row.detail?' title="'+esc(row.note)+'"':'')+' data-source="'+esc(row.record.id)+'"'+binding(task,esc)+'>'+
   '<span class="source-library-label"><strong>'+name+'</strong>'+
    (!grouped&&entry.fields?'<span>'+esc(entry.fields)+'</span>':'')+'</span>'+
   '<span class="source-library-scope">'+
   (subject&&(!grouped||date)?'<span>'+esc(subject)+'</span>':'')+
   (!grouped&&date?'<span class="source-library-date">'+date+'</span>':'')+
   (sequence&&(!grouped||date||subject)?'<span>'+esc(sequence)+'</span>':'')+
   (row.detail?'<span class="source-library-note">'+esc(row.detail)+'</span>':'')+'</span>'+
   '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m8 5 5 5-5 5"/></svg></button></li>';
 }).join('')+'</ul></div>';
}
function sourceCatalog(task,items,ctx){
 const esc=ctx.esc,available=records(task);
 const cited=new Set(scope(paper(task)?.logics,task).flatMap(logic=>list(logic.source_refs)));
 const sections=new Map(),diagnostics=new Map(),byRecord=new Map();
 for(const item of items){
  if(!byRecord.has(item.artifact))byRecord.set(item.artifact,[]);
  byRecord.get(item.artifact).push(item);
 }
 for(const record of available){
  const used=byRecord.get(record.id)||[];
  const dates=[...new Set(used.map(item=>dateKey(item.period)).filter(period=>period&&period!=='期间未记录'))].sort();
  const issues=list(task.draft_source_issues).filter(issue=>issue.artifact_id===record.id);
  const linked=cited.has(record.id);
  const excluded=issues.some(issue=>!issue.partial)||(issues.length>0&&!used.length&&!linked);
  const period=dates.length?dates.length===1?dates[0]:dates[0]+' 至 '+dates.at(-1):'';
  const start=calendarDay(record.parameters?.startdate),end=calendarDay(record.parameters?.enddate);
  const requestWindow=!period&&start&&end&&start<=end?'请求'+(start===end?start:start+' 至 '+end):'';
  const labels=[...new Set(used.map(item=>item.label))];
  const codes=[...new Set(used.map(item=>item.object).filter(value=>typeof value==='string'&&value))];
  const objects=codes.length>2?codes.slice(0,2).join('、')+'等'+codes.length+'个':codes.join('、');
  const inherited=Number.isInteger(record.retained_from_version)&&record.retained_from_version>0&&record.retained_from_version<task.version;
  const adopted=!excluded&&(used.length>0||linked);
  const provenance=inherited?(adopted?'沿用版本':'版本')+record.retained_from_version+(adopted?'':'留存'):'';
  const navigation=navigationRecord(record);
  const usage=excluded?'未用于报告':used.length?used.length+'条关联记录':linked?'研究逻辑引用':navigation?'':record.operation==='get_limit_policy'?'接口检查记录':'未关联研究数值';
  const statements=[...new Set(used.map(item=>requestedStatement(item,record)).filter(Boolean))];
  const scopeNotes=[provenance,statements.length?'请求'+statements.join('、'):''];
  const detail=[...scopeNotes,!excluded&&used.length?'':usage].filter(Boolean).join('，');
  const note=[...scopeNotes,usage].filter(Boolean).join('，');
  const name=ctx.sourceRecordName(record),provider=record.kind==='api'?providerName(record):'';
  const operation=provider&&name.startsWith(provider)?name.slice(provider.length).trim()||name:name;
  const financial=record.source==='ifind'&&record.operation==='basic_data_service'&&used.some(item=>financialFields.has(item.fact.field));
  const title=record.kind==='api'&&labels.length===1&&used.some(item=>item.fact.field)?labels[0]:financial?'公司财务':operation;
  const fields=labels.filter(label=>label!==title).join('、');
  const key=record.kind==='api'?JSON.stringify([record.operation,operation,title,[...labels].sort()]):record.id;
  // Operational lookups belong in the run history unless the research uses them.
  const diagnostic=record.kind==='api'&&record.source==='ifind'&&diagnosticOperations.has(record.operation)&&!used.length&&!linked;
  const category=record.kind!=='api'?'documents':used.length?dataCategory(used[0].fact,record):
   dataCategory({field:requestedFields(record)[0]},record);
  if(!diagnostic&&!sections.has(category))sections.set(category,new Map());
  const catalog=diagnostic?diagnostics:sections.get(category);
  if(!catalog.has(provider))catalog.set(provider,new Map());
  const entries=catalog.get(provider),rank=adopted?0:navigation?2:1;
  if(!entries.has(key))entries.set(key,{title,fields,rank,rows:[]});
  const entry=entries.get(key);
  entry.rank=Math.min(rank,entry.rank);
  entry.rows.push({record,name,objects,period,requestWindow,detail,note});
 }
 const catalogHTML=catalog=>'<div class="source-library-list">'+[...catalog].map(([provider,entries])=>
   '<div class="source-library-provider">'+(provider?'<h4>'+esc(provider)+'</h4>':'')+
   [...entries.values()].sort((a,b)=>a.rank-b.rank).map(entry=>libraryEntry(entry,task,esc)).join('')+'</div>'
  ).join('')+'</div>';
 return {sections:new Map([...sections].map(([category,catalog])=>[category,catalogHTML(catalog)])),
  diagnostics:diagnostics.size?'<section class="desk-block source-diagnostics"><h3>接口记录</h3>'+catalogHTML(diagnostics)+'</section>':''};
}
global.RHResearchData=Object.freeze({list,scope,paper,researchGaps,reportCheckpoint,taskStatus,draftDataRefs,draftFacts,draftEvidence,draftExcerpts,draftExcerptHTML,binding,knowledgeEntries,validTrackingRule,reportConditions,summary,records,observations,numeric,groups,grid,cell,number,seriesData,calcButton,sourceNotes,sourceCitation,sourceCatalog,unitText,providerName,dataCategories,dataCategory,categoryNeeds,recentReadings});
})(window);
