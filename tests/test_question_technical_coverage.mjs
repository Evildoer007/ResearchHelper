/* Exact production view function, isolated synthetic state; no research calls. */
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../products/app/frontend/workspace.js',import.meta.url),'utf8');
const code=source.slice(source.indexOf('function questionCoverageView('),source.indexOf('function overview(',source.indexOf('function questionCoverageView(')));
const context={esc:value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),evidenceButton:()=>'<button>证据</button>'};
vm.createContext(context);vm.runInContext(code,context);
const task={id:'task',version:1,input_hash:'input',market_coverage_basis_version:1,
  source_records:[{id:'calendar',task_id:'task',research_version:1,source:'ifind',operation:'get_trade_dates'},
    {id:'query',task_id:'task',research_version:1,source:'ifind',operation:'cmd_history_quotation'}]};
const question={id:'q1',evidence_need_scopes:{'日历核对':'market_calendar_coverage','取数记录':'query_provenance'},
  evidence_coverage:{task_id:'task',research_version:1,input_hash:'input',question_id:'q1',items:[
    {text:'价格',refs:['price']},{text:'日历核对',refs:[],calendar_source_ids:['calendar']},
    {text:'取数记录',refs:[],provenance_ids:['query']}]}};
const render=(q=question,t=task)=>context.questionCoverageView(q,t);
assert(render().includes('依据覆盖3/3'));assert(render().includes('日历来源'));
assert(render().includes('data-source="calendar"'));assert(render().includes('取数记录'));
assert(render().includes('aria-label="查看日历核对的来源"'));
const old=structuredClone(task);delete old.market_coverage_basis_version;
assert(render(question,old).includes('依据覆盖2/3'));
const foreign=structuredClone(task);foreign.source_records[0].task_id='foreign';
assert(render(question,foreign).includes('依据覆盖2/3'));
const wrong=structuredClone(task);wrong.source_records[0].operation='cmd_history_quotation';
assert(render(question,wrong).includes('依据覆盖2/3'));
const mislabeled=structuredClone(question);mislabeled.evidence_coverage.items[1].refs=['price'];
assert(render(mislabeled,wrong).includes('依据覆盖2/3'));
const stale=structuredClone(question);stale.evidence_coverage.input_hash='stale';assert.equal(render(stale),'');
const untyped=structuredClone(question);delete untyped.evidence_need_scopes;assert(render(untyped).includes('依据覆盖1/3'));
console.log(JSON.stringify({status:'scoped_passed',synthetic:true,externalCalls:0,checks:9}));
