/* Report preview recovery owns presentation only, never task execution. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function harness(){
 const timers=new Map();let clock=0,currentFrame=null,hasPanel=true;
 const documentMock={activeElement:null,body:{},createElement:()=>({content:panel})};
 const title={textContent:''},message={textContent:''},retry={focus(){documentMock.activeElement=retry}},print={disabled:false};let retryHidden=true;
 Object.defineProperty(retry,'hidden',{get:()=>retryHidden,set:value=>{retryHidden=value;if(value&&documentMock.activeElement===retry)documentMock.activeElement=documentMock.body}});
 const panel={hidden:true,dataset:{},contains:element=>element===retry||element===title||element===message,querySelector:s=>s.includes('title')?title:s.includes('message')?message:retry};
 const pane={querySelector:s=>s==='#report-frame'?currentFrame:hasPanel?panel:null,querySelectorAll:()=>[print]};
 const box={window:{},document:documentMock,setTimeout:fn=>{timers.set(++clock,fn);return clock},clearTimeout:id=>timers.delete(id)};
 vm.runInNewContext(fs.readFileSync('products/app/frontend/ui/desk-report.js','utf8'),box);
 const reader=box.window.RHReportReader.create(pane);
 const doc=(options={})=>({URL:'http://local/report',readyState:'complete',contentType:'text/html',title:'研究报告',body:{textContent:'完整研究报告'},querySelector:()=>null,...options});
 function frame(attrs={src:'/saved-report'}){
  const listeners=new Map();const value={attrs:{...attrs},doc:doc({URL:'about:blank',body:{textContent:''}}),isConnected:true,hidden:false,navigations:0,
   focus(){documentMock.activeElement=this},get contentDocument(){return this.doc},hasAttribute:key=>Object.hasOwn(value.attrs,key),getAttribute:key=>value.attrs[key],setAttribute:(key,v)=>value.attrs[key]=v,
   addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name),before:()=>hasPanel=true,
   emit:name=>listeners.get(name)?.({currentTarget:value}),get listeners(){return listeners},
   set src(v){this.attrs.src=v;this.navigations++},set srcdoc(v){this.attrs.srcdoc=v;this.navigations++}};
  return value;
 }
 return {reader,panel,title,message,retry,print,doc,frame,timers,document:documentMock,set(frame){currentFrame=frame},withoutPanel(){hasPanel=false}};
}
test('initial about:blank remains loading and cannot print an error or blank page',()=>{const h=harness(),f=h.frame();h.set(f);h.reader.updated();assert.equal(h.panel.dataset.state,'loading');assert.equal(h.retry.hidden,true);assert.equal(h.print.disabled,true);assert.equal(h.reader.isReady(f),false);h.reader.destroy()});
test('failed response recovers by reading the same saved URL without changing content',()=>{const h=harness(),f=h.frame();h.set(f);h.reader.updated();f.doc=h.doc({contentType:'application/json',body:{textContent:'{"error":"private backend reason"}'}});f.emit('load');assert.equal(h.panel.dataset.state,'error');assert.equal(f.hidden,true);assert.equal(h.retry.hidden,false);assert.doesNotMatch(h.message.textContent,/private/);h.reader.retry();assert.equal(f.attrs.src,'/saved-report');assert.equal(f.navigations,1);f.doc=h.doc();f.emit('load');assert.equal(h.reader.isReady(f),true);assert.equal(f.hidden,false);assert.equal(h.print.disabled,false);h.reader.destroy()});
test('srcdoc retains its exact frozen HTML across recovery',()=>{const h=harness(),f=h.frame({srcdoc:'<h1>公开完整内容</h1>'});h.set(f);h.reader.updated();assert.equal(h.panel.dataset.state,'loading');h.reader.retry();assert.equal(f.attrs.srcdoc,'<h1>公开完整内容</h1>');f.doc=h.doc({URL:'about:srcdoc'});f.emit('load');assert.equal(h.reader.isReady(f),true);h.reader.destroy()});
test('late response from a previous frame cannot alter the new task reader',()=>{const h=harness(),old=h.frame(),fresh=h.frame({src:'/other-task'});h.set(old);h.reader.updated();const late=old.listeners.get('load');h.set(fresh);h.reader.updated();old.isConnected=false;old.doc=h.doc({contentType:'application/json'});late({currentTarget:old});assert.equal(h.panel.dataset.state,'loading');assert.equal(h.reader.isReady(fresh),false);fresh.doc=h.doc();fresh.emit('load');assert.equal(h.reader.isReady(fresh),true);h.reader.destroy()});
test('empty saved documents receive an actionable failure instead of a blank reader',()=>{const h=harness(),f=h.frame();h.set(f);h.reader.updated();f.doc=h.doc({body:{textContent:''}});f.emit('load');assert.equal(h.panel.dataset.state,'error');assert.equal(h.retry.hidden,false);h.reader.destroy()});
test('file-origin document access restrictions do not preempt the load event',()=>{const h=harness(),f=h.frame({src:'reports/history.html'});f.doc=null;h.set(f);h.reader.updated();assert.equal(h.reader.isReady(f),false);f.emit('load');assert.equal(h.reader.isReady(f),true);h.reader.destroy()});
test('a historical report without a state panel gets the same recovery controls',()=>{const h=harness(),f=h.frame();h.withoutPanel();h.set(f);h.reader.updated();f.emit('error');assert.equal(h.panel.dataset.state,'error');assert.equal(h.retry.hidden,false);h.reader.destroy()});
test('timeout and disposal release handlers without invoking research or navigation',()=>{const h=harness(),f=h.frame();h.set(f);h.reader.updated();[...h.timers.values()][0]();assert.equal(h.panel.dataset.state,'error');h.reader.destroy();assert.equal(h.timers.size,0);assert.equal(f.listeners.size,0);assert.equal(f.navigations,0);h.reader.retry();assert.equal(f.navigations,0)});
test('reader without a selected report safely ignores retry',()=>{const h=harness();h.reader.updated();h.reader.retry();assert.equal(h.reader.isReady(null),false);h.reader.destroy()});

test('retry restores focus after loading hides its originally focused button',()=>{const h=harness(),f=h.frame();h.set(f);h.reader.updated();f.emit('error');h.retry.focus();h.reader.retry();assert.equal(h.document.activeElement,h.document.body);f.doc=h.doc();f.emit('load');assert.equal(h.document.activeElement,f);h.reader.destroy()});
