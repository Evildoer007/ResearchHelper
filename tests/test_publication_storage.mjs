/** Independent storage tests; no browser, model, or network. */
import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../products/app/frontend/workspace.js',import.meta.url),'utf8');
const start=source.indexOf('function offlineTasksForStorage(){'),end=source.indexOf('function remember(){',start);
assert(start>=0&&end>start);const context=vm.createContext({});
vm.runInContext(`let boot={},tasks=[];${source.slice(start,end)}`,context);
vm.runInContext(`
const seed={id:'seed',synthetic:true,fixture:'etf',questions:[],ledger:{},report_html:'X'.repeat(300000),report_detailed_html:'Y'.repeat(400000)};
boot.tasks=[seed];tasks=[seed,{id:'own',report_html:'keep legacy content'}];
const before=JSON.stringify(tasks),saved=offlineTasksForStorage();
if(JSON.stringify(tasks)!==before)throw Error('storage changed live tasks');
if(JSON.stringify(saved).length>1000)throw Error('packaged reports duplicated in storage');
if(saved[0].report_html!==undefined||saved[0].report_detailed_html!==undefined)throw Error('HTML persisted');
if(saved[1].report_html!=='keep legacy content')throw Error('legacy report discarded');
const restored=restoreOfflineTask(saved[0],[seed]);
if(restored.report_html!==seed.report_html||restored.report_detailed_html!==seed.report_detailed_html)throw Error('two report profiles not restored');
const missing={id:'unavailable',demo_report_ref:'unknown'};
if(restoreOfflineTask(missing,[seed])!==missing)throw Error('unknown task overwritten');
const changed={...seed,id:'changed',report_html:'independent frozen report'};tasks=[changed];
if(offlineTasksForStorage()[0]!==changed)throw Error('independent content discarded');
`,context);
console.log(JSON.stringify({status:'passed',checks:7,model_calls:0,data_requests:0}));
