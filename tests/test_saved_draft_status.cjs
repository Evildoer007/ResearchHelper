/* A saved draft never becomes an approved judgment or hides a failed run. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const box={window:{}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../products/app/frontend/ui/research-data.js'),'utf8'),box);
const vectors=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/saved-draft-status.json'),'utf8'));
for(const row of vectors.cases)test(row.name,()=>{
 const task={...structuredClone(vectors.base),...row.changes};
 if(row.delivery_changes)task.delivery={...task.delivery,...row.delivery_changes};
 const before=JSON.stringify(task),result=box.window.RHResearchData.taskStatus(task);
 assert.equal(result.label,row.label);assert.equal(result.processLabel,row.process);assert.equal(result.tone,row.tone);
 assert.equal(result.status,task.status);assert.equal(JSON.stringify(task),before);
 if(row.label==='草稿已保存'){
  if(row.tone==='attention')assert.equal(result.title,task.draft_report.readout_gaps.join('；'));
  else assert.match(result.title,/草稿/);
  assert.doesNotMatch(result.label,/核验通过|独立研究完成/);
 }
 result.label='changed local projection';assert.equal(JSON.stringify(task),before);
});
