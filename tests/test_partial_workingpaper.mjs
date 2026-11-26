/* Check rejected-source grouping without a retained run or an App session. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';

const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const source=await readFile(path.join(root,'products/app/frontend/workspace.js'),'utf8');
const task={id:'synthetic-workingpaper',version:1};
const original={id:'synthetic-quote',kind:'api',source:'ifind',operation:'cmd_history_quotation',
  task_id:task.id,research_version:task.version};
task.source_records=[original];
task.rejected_evidence=Array.from({length:61},(_,index)=>({id:'sample-'+(index+1),
  artifact_id:original.id,object:'600000.SH',rejection:'证券身份映射valid_from缺失',question_ids:['q1']}));
const context={window:{},esc:value=>String(value??'').replace(/[&<>"']/g,character=>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]))};
vm.createContext(context);
vm.runInContext(await readFile(path.join(root,'products/app/frontend/ui/research-data.js'),'utf8'),context);
for(const [start,end] of [['collectionWorkingpaper','conclusion'],['sourceRecordName','sourceRecordButtons'],['sourceRecordButtons','openSource']]){
  const first=source.indexOf('function '+start+'('),last=source.indexOf('function '+end+'(',first);
  assert.ok(first>=0&&last>first,'production UI function exists: '+start);
  vm.runInContext(source.slice(first,last),context);
}
const record={status:'failed',checks:[],model_calls:0,provider_calls:0,original_case_modified:false};
const check=(label,value)=>{assert.equal(value,true,label);record.checks.push(label)};
try{
  const before=JSON.stringify(task),paper=context.collectionWorkingpaper(task);
  check('retained raw source remains reachable',paper.includes('留存资料')&&paper.includes(task.source_records[0].id));
  check('61 same-source failures grouped once',paper.includes('61条')&&(paper.match(/缺少代码映射有效期起点/g)||[]).length===1);
  check('raw source never labelled approved',paper.includes('未通过核验，不能作为结论依据。')&&!paper.includes('已核验数据'));
  check('working record never mutates research',JSON.stringify(task)===before);
  const foreign=structuredClone(task);foreign.source_records[0].task_id='another-task';
  check('foreign-task source cannot be opened',!context.collectionWorkingpaper(foreign).includes('data-source=')&&context.collectionWorkingpaper(foreign).includes('原件未留存'));
  const wrongVersion=structuredClone(task);wrongVersion.source_records[0].research_version=task.version+1;
  check('foreign-version source cannot be opened',!context.collectionWorkingpaper(wrongVersion).includes('data-source='));
  const malformed=structuredClone(task);delete malformed.source_records[0].id;
  check('missing original id cannot produce dead button',!context.collectionWorkingpaper(malformed).includes('data-source='));
  const separate=structuredClone(task);separate.rejected_evidence[0].question_ids=['a-different-question'];
  check('separate questions are not merged',context.collectionWorkingpaper(separate).includes('60条')&&context.collectionWorkingpaper(separate).includes('1条'));
  const hostile=structuredClone(task);hostile.rejected_evidence=[{id:'test',artifact_id:task.source_records[0].id,object:'<script>bad()</script>',rejection:'<img src=x onerror=bad()>',question_ids:[]}];
  const escaped=context.collectionWorkingpaper(hostile);
  check('untrusted failure text stays literal',escaped.includes('&lt;img')&&!escaped.includes('<img')&&!escaped.includes('<script>'));
  check('empty task has no artificial sources',context.collectionWorkingpaper({id:'blank',version:1})==='');
  record.status='passed';
}catch(error){record.error=error.message;process.exitCode=1}
console.log(JSON.stringify({...record,checks:record.checks.length}));
