const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const sandbox={window:{}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../products/app/frontend/ui/research-data.js'),'utf8'),sandbox);
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../products/app/frontend/ui/desk-sources.js'),'utf8'),sandbox);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ctx={esc,sourceRecordName:r=>r.name,sourceRecordButtons:t=>t.source_records.map(r=>'<button data-source="'+esc(r.id)+'">'+esc(r.name)+'</button>').join(''),unverifiedProposal:(_t,r)=>r,processDetails:()=>'<h2>实际执行计划</h2><p>运行账本</p>'};
const render=t=>sandbox.window.RHDeskViews.sources({source_records:[],...t},ctx);
test('draft values and original links precede collapsed process',()=>{
 const html=render({source_records:[{id:'a',name:'财务快照'}],draft_report:{facts:[{id:'f',code:'600000',label:'营业收入',period:'2025',raw_value:0,unit:'元',artifact_id:'a'}]}});
 assert.ok(html.indexOf('营业收入')<html.indexOf('实际执行计划'));
 assert.match(html,/data-source="a"/);assert.match(html,/>0<\/button>/);assert.match(html,/>2025<\/time>/);
 assert.match(html,/<details[^>]*data-reading-key="source-process"[^>]*>/);assert.doesNotMatch(html,/<details[^>]*\bopen\b/);
});
test('missing units remain explicit without verification claims',()=>{
 const html=render({draft_report:{facts:[{id:'x',label:'收入',value:120,raw_unit:'',unit:'元'}]}});
 assert.match(html,/单位待核对/);assert.match(html,/期间未记录/);assert.doesNotMatch(html,/data-draft-source=/);
 assert.doesNotMatch(html,/核验通过|已核验|>元</);
});
test('a retained original is not a verified fact; empty tasks stay usable',()=>{
 const html=render({source_records:[{id:'a',name:'已下载公告'}]});
 assert.match(html,/原始资料/);assert.doesNotMatch(html,/已纳入证据|核验通过/);
 assert.match(render({}),/取得的数据和材料会显示在这里/);assert.doesNotMatch(render({}),/阻断|禁止/);
});
test('unverified excerpts, escaping and distinct periods survive',()=>{
 const html=render({evidence:[{id:'a',text:'<script>',value:2,unit:'%',period:'2024'}],rejected_evidence:[{id:'b',text:'收入',value:3,unit:'%',period:'2025',rejection:'公开时间缺失'}]});
 assert.match(html,/&lt;script&gt;/);assert.match(html,/2024/);assert.match(html,/2025/);assert.match(html,/待核验/);assert.match(html,/data-evidence="b"/);
});

test('a partial source still shows its real linked observations',()=>{
 const html=render({source_records:[{id:'a',name:'财务快照'}],draft_source_issues:[{artifact_id:'a',partial:true,reason:'部分行不对应请求'}],draft_report:{facts:[{id:'D1',code:'600519.SH',label:'营业收入',period:'20251231',raw_value:100,unit:'元',artifact_id:'a'}]}});
 assert.match(html,/1条关联记录/);assert.doesNotMatch(html,/未用于报告/);assert.match(html,/data-source="a"/);
});
test('a partial response with no usable observations is not described as linked',()=>{
 const html=render({source_records:[{id:'a',name:'财务快照'}],draft_source_issues:[{artifact_id:'a',partial:true}],draft_report:{facts:[]}});
 assert.match(html,/未用于报告/);assert.doesNotMatch(html,/条关联记录/);assert.match(html,/data-source="a"/);
});
test('a fully excluded response cannot become used merely through stale associations',()=>{
 const html=render({source_records:[{id:'a',name:'财务快照'}],draft_source_issues:[{artifact_id:'a',partial:false}],draft_report:{facts:[{id:'D1',label:'营业收入',raw_value:100,artifact_id:'a'}]}});
 assert.match(html,/未用于报告/);assert.doesNotMatch(html,/1条关联记录/);
});

test('fund NAV uses its own business heading',()=>{
 const html=render({source_records:[{id:'a',name:'上交所ETF申赎清单',source:'official',operation:'etf_pcf'}],draft_report:{facts:[{id:'D1',code:'510300.SH',field:'unit_nav',label:'单位净值',period:'2026-10-21',raw_value:'4.1856',unit:'元',artifact_id:'a'}]}});
 assert.match(html,/data-category="fund"><h3>基金与指数<\/h3>/);
 assert.match(html,/aria-label="基金净值数据对照"/);
});


test('adopted inherited values show their previous version without claiming a new fetch',()=>{
 const html=render({id:'task',version:2,source_records:[{id:'a',task_id:'task',research_version:2,retained_from_version:1,name:'财务快照'}],draft_report:{facts:[{id:'D1',label:'营业收入',raw_value:100,artifact_id:'a'}]}});
 assert.match(html,/沿用版本1，1条关联记录/);assert.doesNotMatch(html,/已刷新|最新取数/);
});
test('an unadopted old original stays accessible and is distinguished from a new response',()=>{
 const html=render({id:'task',version:2,source_records:[{id:'a',task_id:'task',research_version:2,retained_from_version:1,name:'旧财务快照'},{id:'b',task_id:'task',research_version:2,name:'本次财务快照'}],draft_report:{facts:[{id:'D1',label:'营业收入',raw_value:101,artifact_id:'b'}]}});
 assert.match(html,/版本1留存，未关联研究数值/);assert.match(html,/1条关联记录/);assert.match(html,/data-source="a"/);assert.match(html,/data-source="b"/);
});
test('a cited text original is associated with its research logic even without numeric rows',()=>{
 const original={id:'a',task_id:'task',research_version:2,retained_from_version:1,name:'合成合作纪要'};
 const task={id:'task',version:2,materials:[original],draft_report:{facts:[],logics:[{title:'合作条件',source_refs:['a','a']}]}};
 const html=render(task);
 assert.match(html,/沿用版本1，研究逻辑引用/);
 assert.equal((html.match(/data-source="a"/g)||[]).length,1);
 assert.doesNotMatch(html,/未关联研究数值|已核验/);
 const oldDraft={...task,draft_report:{...task.draft_report,research_version:1}};
 assert.match(render(oldDraft),/版本1留存，未关联研究数值/);
 assert.doesNotMatch(render(oldDraft),/研究逻辑引用/);
});
test('an excerpt opens its exact source with task and evidence binding',()=>{
 const task={id:'task',version:2,materials:[{id:'a',task_id:'task',research_version:2,name:'合成合作纪要'}],
  evidence:[{id:'e',text:'须完成验收。',artifact_id:'a',record_locator:{part:'P1'}}]};
 const html=render(task);
 assert.match(html,/class="source-excerpt"[\s\S]*data-source="a" data-source-evidence="e" data-source-task="task" data-source-version="2"/);
 assert.doesNotMatch(render({...task,materials:[]}),/data-source-evidence="e"/);
});

test('all eight registered company financial fields share the financial section',()=>{
 const fields=['ths_revenue_stock','ths_operating_cost_stock','ths_np_stock','ths_np_atoopc_stock','ths_ncf_from_oa_stock','ths_account_receivable_stock','ths_inventory_stock','ths_accounts_payable_stock'];
 const html=render({source_records:[{id:'finance',name:'iFinD基础数据',source:'ifind',operation:'basic_data_service'}],draft_report:{facts:fields.map((field,index)=>({id:'D'+index,code:'002594.SZ',field,label:field,period:'2026-06-30',raw_value:index+1,artifact_id:'finance',unit:'原始单位待核对'}))}});
 assert.equal((html.match(/data-category="financial"><h3>财务<\/h3>/g)||[]).length,1);
 const task={source_records:[{id:'finance',name:'财务快照'}]};const data=sandbox.window.RHResearchData;
 const groups=data.groups(data.observations(fields.map((field,index)=>({id:'D'+index,field,code:'002594.SZ',raw_value:index+1,artifact_id:'finance'}))),task);
 assert.equal(groups.length,1);assert.equal(groups[0].items.length,8);
});
test('quote currency is displayed without filling unknown or missing units',()=>{
 const fact={id:'D1',code:'0700.HK',source:'ifind',field:'close',label:'收盘价',period:'2026-10-30',raw_value:328.3386,artifact_id:'price',unit:'元',currency:'RMB',unit_reference:{kind:'registered_field',registry:'ifind.cmd_history_quotation',family:'price',field:'close',currency_parameter:'RMB'}};
 const task={source_records:[{id:'price',name:'iFinD历史行情',source:'ifind',operation:'cmd_history_quotation'}],draft_report:{facts:[fact]}};
 assert.match(render(task),/人民币元/);
 for(const change of [{currency:undefined},{unit:'点'},{raw_unit:''},{unit_reference:null}]){
  const html=render({...task,draft_report:{facts:[{...fact,...change}]}});assert.doesNotMatch(html,/人民币元/);
 }
});

test('candidate and identity originals retain links without irrelevant numeric warnings',()=>{
 const record={id:'candidates',kind:'api',source:'ifind',operation:'smart_stock_picking',status:'成功待核验',name:'iFinD候选发现',parameters:{searchstring:'证券行业股票',searchtype:'stock'}};
 let html=render({source_records:[record,{id:'identity',kind:'api',source:'ifind',operation:'get_thscode',status:'成功待核验',name:'iFinD证券代码查询'}]});
 assert.match(html,/data-source="candidates"/);assert.match(html,/data-source="identity"/);assert.doesNotMatch(html,/未关联研究数值|未用于报告|条关联记录/);
 assert.doesNotMatch(html,/<div class="source-library-period">/);
 html=render({id:'task',version:2,source_records:[{...record,retained_from_version:1}]});
 assert.match(html,/版本1留存/);assert.doesNotMatch(html,/版本1留存，<|未关联研究数值|沿用版本/);
});
test('failed navigation still exposes exclusion and preserves its original',()=>{
 const html=render({source_records:[{id:'candidate',kind:'api',source:'ifind',operation:'smart_stock_picking',status:'返回结构异常',name:'候选查询'}],draft_source_issues:[{artifact_id:'candidate',reason:'返回列未对齐'}]});
 assert.match(html,/未用于报告/);assert.match(html,/data-source="candidate"/);assert.doesNotMatch(html,/核验通过|条关联记录/);
});
test('financial queries and forged discovery labels cannot hide unused numeric originals',()=>{
 const html=render({source_records:[{id:'financial',kind:'api',source:'ifind',operation:'smart_stock_picking',status:'成功待核验',name:'财务查询',parameters:{searchstring:'600519.SH2025年年报营业收入',searchtype:'stock'},financial_query_contract:{query_kind:'object_discovery'}}]});
 assert.match(html,/未关联研究数值/);assert.match(html,/data-source="financial"/);
});
test('matching API requests share a visible label and keep each original and reporting scope',()=>{
 const periods=['2024-12-31','2025-12-31'],types=['1','2'],fields=['ths_revenue_stock','ths_np_atoopc_stock'];
 const source_records=periods.map((period,index)=>({id:'financial'+index,kind:'api',source:'ifind',
  operation:'basic_data_service',name:'iFinD 基础数据',
  parameters:{codes:'600519.SH',indipara:fields.map(indicator=>({indicator,indiparams:[period,types[index]]}))}}));
 const facts=periods.flatMap((period,index)=>fields.map(field=>({id:field+index,field,period,period_basis:'请求D报告期',
  code:'600519.SH',artifact_id:source_records[index].id,raw_value:12,unit:'元'})));
 const html=render({id:'test',version:2,source_records,draft_report:{facts}});
 const library=html.slice(html.indexOf('class="source-library desk-disclosure"'),html.indexOf('class="source-process'));
 assert.equal((library.match(/<h4>iFinD<\/h4>/g)||[]).length,1);
 assert.equal((library.match(/<strong>公司财务<\/strong>/g)||[]).length,1);
 assert.equal((library.match(/class="source-library-open"/g)||[]).length,2);
 assert.match(library,/data-source="financial0" data-source-task="test" data-source-version="2"[\s\S]*?<time datetime="2024-12-31">2024-12-31<\/time>[\s\S]*?请求合并报表/);
 assert.match(library,/data-source="financial1" data-source-task="test" data-source-version="2"[\s\S]*?<time datetime="2025-12-31">2025-12-31<\/time>[\s\S]*?请求母公司报表/);
});

test('operational originals appear in run history unless cited by the current research',()=>{
 const source_records=[
  {id:'quota',kind:'api',source:'ifind',operation:'get_limit_policy',name:'iFinD 接口限额',status:'成功待核验'},
  {id:'calendar',kind:'api',source:'ifind',operation:'get_trade_dates',name:'iFinD 交易日历',status:'成功待核验'},
  {id:'quotes',kind:'api',source:'ifind',operation:'cmd_history_quotation',name:'iFinD 历史行情',status:'成功待核验'}
 ];
 const task={id:'test',version:2,source_records,draft_report:{logics:[{source_refs:['calendar']}]}};
 const [originals,process]=render(task).split('<details class="source-process');
 assert.equal((originals.match(/class="source-library-open"/g)||[]).length,2);
 assert.match(originals,/data-source="calendar"/);assert.match(originals,/data-source="quotes"/);
 assert.doesNotMatch(originals,/data-source="quota"/);
 assert.match(process,/接口记录[\s\S]*data-source="quota" data-source-task="test" data-source-version="2"/);
 assert.doesNotMatch(process,/data-source="calendar"|data-source="quotes"/);
});
