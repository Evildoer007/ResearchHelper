/** Independent storage tests; no browser, model, or network. */
import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../products/app/frontend/workspace.js',import.meta.url),'utf8');
const start=source.indexOf('function offlineTasksForStorage('),end=source.indexOf('function draftObject(',start);
assert(start>=0&&end>start);const context=vm.createContext({});
const binding=source.indexOf('function samePackagedEdition(');
vm.runInContext(`let boot={};${source.slice(binding>=0?binding:start,end)}`,context);
vm.runInContext(`
const seed={id:'seed',version:1,synthetic:true,fixture:'etf',questions:[],ledger:{},delivery:{version:1,digest:'a'.repeat(64),html_sha256:'b'.repeat(64)},report_html:'X'.repeat(300000),report_detailed_html:'Y'.repeat(400000)};
boot.tasks=[seed];const tasks=[seed,{id:'own',report_html:'keep legacy content'}];
const before=JSON.stringify(tasks),saved=offlineTasksForStorage(tasks);
if(JSON.stringify(tasks)!==before)throw Error('storage changed live tasks');
if(JSON.stringify(saved).length>1000)throw Error('packaged reports duplicated in storage');
if(saved[0].report_html!==undefined||saved[0].report_detailed_html!==undefined)throw Error('HTML persisted');
if(saved[1].report_html!=='keep legacy content')throw Error('legacy report discarded');
const restored=restoreOfflineTask(saved[0],[seed]);
if(restored.report_html!==seed.report_html||restored.report_detailed_html!==seed.report_detailed_html)throw Error('two report profiles not restored');
const missing={id:'unavailable',demo_report_ref:'unknown'};
if(restoreOfflineTask(missing,[seed])!==missing)throw Error('unknown task overwritten');
const changed={...seed,id:'changed',report_html:'independent frozen report'};
if(offlineTasksForStorage([changed])[0]!==changed)throw Error('independent content discarded');
const revised={...seed,id:'new-package',delivery:{...seed.delivery,digest:'c'.repeat(64)},report_html:'new report'};
if(restoreOfflineTask(saved[0],[revised])!==saved[0])throw Error('new package attached to an old task');
if(restoreOfflineTask(saved[0],[{...revised,id:seed.id}])!==saved[0])throw Error('same task ID attached to a different edition');
const explicit={...saved[0],report_html:'explicit copy from local backup'};
if(restoreOfflineTask(explicit,[seed])!==explicit)throw Error('user copy replaced by template');
`,context);
console.log(JSON.stringify({status:'passed',checks:10,model_calls:0,data_requests:0}));
