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
 assert.match(render({}),/取得的数据和资料会保留在这里/);assert.doesNotMatch(render({}),/阻断|禁止/);
});
test('unverified excerpts, escaping and distinct periods survive',()=>{
 const html=render({evidence:[{id:'a',text:'<script>',value:2,unit:'%',period:'2024'}],rejected_evidence:[{id:'b',text:'收入',value:3,unit:'%',period:'2025',rejection:'公开时间缺失'}]});
 assert.match(html,/&lt;script&gt;/);assert.match(html,/2024/);assert.match(html,/2025/);assert.match(html,/待核验/);assert.match(html,/data-evidence="b"/);
});
