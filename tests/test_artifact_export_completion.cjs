const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../products/app/frontend/ui/artifact-export.js'),'utf8');
function fixture(native=true){
 const listeners={},messages=[],notices=[],downloads=[],buttons=[{disabled:false,setAttribute(){},removeAttribute(){}}];
 const window={addEventListener:(name,fn)=>listeners[name]=fn};
 if(native)window.webkit={messageHandlers:{researchhelper:{postMessage:msg=>messages.push(msg)}}};
 vm.runInNewContext(source,{window,document:{querySelectorAll:()=>buttons,createElement:()=>({click(){downloads.push(this.download)}})},URL:{createObjectURL:()=> 'blob:local',revokeObjectURL(){}},Blob,TextDecoder,Uint8Array,atob,Date,Map,setTimeout:fn=>fn()});
 const api=window.RHArtifactExport.create({notify:msg=>notices.push(msg)});
 return {api,messages,notices,downloads,buttons,emit:detail=>listeners['researchhelper:artifact-saved']({detail})};
}
for(const status of ['saved','cancelled','failed','rejected'])test('native '+status+' settles only after matching completion',async()=>{
 const f=fixture();let settled=false;
 const promise=f.api.saveText('研究.html','正文').then(result=>{settled=true;return result});
 assert.equal(f.messages[0].action,'save');assert.ok(f.messages[0].operation_id);
 await Promise.resolve();assert.equal(settled,false);
 f.emit({operation_id:'foreign',status});await Promise.resolve();assert.equal(settled,false);
 f.emit({operation_id:f.messages[0].operation_id,status,name:'研究.html'});
 assert.equal((await promise).status,status);assert.equal(f.notices.length,1);
 if(status!=='saved')assert.doesNotMatch(f.notices[0],/^已保存/);
});
test('run prevents duplicate export and restores buttons after cancellation',async()=>{
 const f=fixture();let calls=0;
 const first=f.api.run(()=>{calls++;return f.api.saveText('a.html','a')});
 assert.equal(f.buttons[0].disabled,true);
 await f.api.run(()=>{calls++;return f.api.saveText('b.html','b')});assert.equal(calls,1);
 f.emit({operation_id:f.messages[0].operation_id,status:'cancelled'});await first;
 assert.equal(f.buttons[0].disabled,false);
 await f.api.run(()=>{calls++;return Promise.resolve()});assert.equal(calls,2);
});
test('UTF8 HTML base64 keeps Chinese and uses text save',async()=>{
 const f=fixture(),html='<h1>研究报告：现金流</h1>';
 const promise=f.api.saveBinary({filename:'研究.html',content_base64:Buffer.from(html).toString('base64')});
 assert.equal(f.messages[0].text,html);assert.equal(f.messages[0].action,'save');
 f.emit({operation_id:f.messages[0].operation_id,status:'saved'});await promise;
});
test('binary export preserves bytes and native action',async()=>{
 const f=fixture(),base64=Buffer.from([0,255,128,1]).toString('base64');
 const promise=f.api.saveBinary({filename:'a.pdf',content_base64:base64,mime:'application/pdf'});
 assert.equal(f.messages[0].action,'save_binary');assert.equal(f.messages[0].base64,base64);
 f.emit({operation_id:f.messages[0].operation_id,status:'saved'});await promise;
});
test('browser reports download start without claiming disk save',async()=>{
 const f=fixture(false),result=await f.api.saveText('研究.html','正文');
 assert.equal(result.status,'download_started');assert.deepEqual(f.downloads,['研究.html']);
 assert.match(f.notices[0],/已开始下载/);assert.doesNotMatch(f.notices[0],/已保存/);
});
test('run restores controls after work throws',async()=>{
 const f=fixture();await assert.rejects(f.api.run(()=>{throw Error('failure')}),/failure/);
 assert.equal(f.buttons[0].disabled,false);
});

test('native completion displays the filename selected in the save panel',async()=>{
 const f=fixture();const promise=f.api.saveText('default.html','正文');
 f.emit({operation_id:f.messages[0].operation_id,status:'saved',name:'客户报告.html'});
 assert.equal((await promise).name,'客户报告.html');assert.equal(f.notices[0],'已保存：客户报告.html');
});
