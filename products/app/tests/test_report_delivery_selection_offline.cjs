/* Fresh report-delivery UI checks; no browser, backend, credentials or network. */
const {readFileSync}=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const {test}=require('node:test');
const source=readFileSync(path.join(__dirname,'../frontend/workspace.js'),'utf8');
const reportCode=source.slice(source.indexOf('function reportURL('),source.indexOf('function trackingConditions('));
const exportCode=source.slice(source.indexOf('async function exportReport('),source.indexOf('\n',source.indexOf('\n}',source.indexOf('async function exportReport('))+2));
function harness(extra={}){
  const context={apiMode:true,selected:{id:'a',version:2,delivery:{version:1},synthetic:false},historical:null,
    taskSelectionGeneration:1,leaving:false,boot:{cases:[]},window:{},gapView:()=>'',
    esc:value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
    downloads:[],requests:[],leaveWorkspace(){this.leaving=true},...extra};
  context.download=(name,text)=>context.downloads.push({name,text});
  context.fetch=async url=>{context.requests.push(url);return {ok:true,status:200,text:async()=>'<html>saved version</html>'}};
  vm.createContext(context);
  vm.runInContext(reportCode+'\n'+exportCode,context);
  return context;
}
test('API research has a truthful concise saved-version label',()=>{
  const h=harness(),paper=h.reportView(h.selected);
  assert.ok(paper.includes('已保存版本1'));
  assert.ok(!paper.includes('用户材料研究'));
  assert.ok(!paper.includes('实时研究'));
  assert.ok(paper.includes('src="/api/tasks/a/report/1"'));
});
test('Synthetic fixtures retain explicit non-live label',()=>{
  const h=harness();h.selected.synthetic=true;
  assert.ok(h.reportView(h.selected).includes('合成验收材料，不是实时研究'));
});
test('Unsaved report never advertises delivery',async()=>{
  const h=harness();h.selected.delivery=null;h.selected.pending_content={};
  assert.ok(h.reportView(h.selected).includes('等待呈现与保存成功'));
  await assert.rejects(h.exportReport(),/报告尚未保存/);
  assert.equal(h.downloads.length,0);assert.equal(h.requests.length,0);
});
test('Export body and filename use the delivered version, not a newer task version',async()=>{
  const h=harness();await h.exportReport();
  assert.deepEqual(h.requests,['/api/tasks/a/report/1']);
  assert.deepEqual(h.downloads,[{name:'研究报告_v1.html',text:'<html>saved version</html>'}]);
});
test('A late export cannot download or rename another selected research',async()=>{
  const h=harness();let release,entered;
  const ready=new Promise(resolve=>entered=resolve);
  h.fetch=async()=>({ok:true,status:200,text:()=>new Promise(resolve=>{release=resolve;entered()})});
  const pending=h.exportReport();await ready;
  h.selected={id:'b',version:3,delivery:{version:3}};h.taskSelectionGeneration++;
  release('<html>task a</html>');await assert.rejects(pending,/研究已切换/);
  assert.equal(h.downloads.length,0);
});
test('Leaving and returning to the same task does not renew an earlier export',async()=>{
  const h=harness();let release,entered;
  const ready=new Promise(resolve=>entered=resolve);
  h.fetch=async()=>({ok:true,status:200,text:()=>new Promise(resolve=>{release=resolve;entered()})});
  const pending=h.exportReport();await ready;h.taskSelectionGeneration+=2;
  release('<html>task a</html>');await assert.rejects(pending,/研究已切换/);
  assert.equal(h.downloads.length,0);
});
test('Offline reading and export prefer saved HTML over pending presentation',async()=>{
  const h=harness({apiMode:false});h.selected.report_html='<html>saved</html>';h.selected.pending_html='<html>pending</html>';
  assert.ok(h.reportView(h.selected).includes('&lt;html&gt;saved&lt;/html&gt;'));
  await h.exportReport();assert.equal(h.downloads[0].text,'<html>saved</html>');
  assert.equal(h.requests.length,0);
});
test('Historical report filename is not labelled with another task version',async()=>{
  const h=harness({historical:{key:'consumer',shortTitle:'消费轮动研究'}});
  await h.exportReport(true);
  assert.deepEqual(h.requests,['/reports/consumer.html']);
  assert.equal(h.downloads[0].name,'消费轮动研究.html');
});
test('Failed reads do not download a report',async()=>{
  const h=harness();h.fetch=async()=>({ok:false,status:500});
  await assert.rejects(h.exportReport(),/报告读取失败/);assert.equal(h.downloads.length,0);
});
