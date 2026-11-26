const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {createHash,webcrypto}=require('node:crypto');
const source=fs.readFileSync(path.join(__dirname,'../products/app/frontend/ui/artifact-export.js'),'utf8');
function receipt(bytes,format,filename,mime){
 return {format,filename,mime,content_base64:bytes.toString('base64'),sha256:createHash('sha256').update(bytes).digest('hex')};
}
function fixture(native=true,windows=false){
 const listeners={},hostListeners={},messages=[],notices=[],downloads=[],buttons=[{disabled:false,setAttribute(){},removeAttribute(){}}];
 let post;
 const posted=new Promise(resolve=>post=resolve);
 const window={addEventListener:(name,fn)=>listeners[name]=fn};
 if(native)window.webkit={messageHandlers:{researchhelper:{postMessage:msg=>{messages.push(msg);post(msg)}}}};
 if(windows)window.chrome={webview:{addEventListener:(name,fn)=>hostListeners[name]=fn}};
 vm.runInNewContext(source,{window,document:{querySelectorAll:()=>buttons,createElement:()=>({click(){downloads.push(this.download)}})},URL:{createObjectURL:()=> 'blob:local',revokeObjectURL(){}},Blob,TextDecoder,Uint8Array,AbortController,atob,Date,Map,crypto:webcrypto,setTimeout:fn=>fn()});
 const api=window.RHArtifactExport.create({notify:msg=>notices.push(msg)});
 return {api,messages,notices,downloads,buttons,posted,emit:detail=>listeners['researchhelper:artifact-saved']({detail}),
  emitWindows:data=>hostListeners.message({data})};
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
test('UTF8 HTML preserves its BOM and Chinese after verification',async()=>{
 const f=fixture(),html='\uFEFF<h1>研究报告：现金流</h1>';
 const done=f.api.saveReport(receipt(Buffer.from(html),'html','研究.html','text/html'),'html',()=>{});
 const message=await Promise.race([f.posted,done]);
 assert.equal(message.text,html);assert.equal(message.action,'save');
 f.emit({operation_id:message.operation_id,status:'saved'});await done;
});
test('binary export preserves bytes and native action',async()=>{
 const f=fixture(),exported=receipt(Buffer.from([0,255,128,1]),'pdf','a.pdf','application/pdf');
 const done=f.api.saveReport(exported,'pdf',()=>{});
 const message=await Promise.race([f.posted,done]);
 assert.equal(message.action,'save_binary');assert.equal(message.base64,exported.content_base64);
 f.emit({operation_id:message.operation_id,status:'saved'});await done;
});
test('a wrong artifact or expired selection never reaches the native panel',async()=>{
 const f=fixture(),exported=receipt(Buffer.from('good'),'html','a.html','text/html');
 await assert.rejects(f.api.saveReport({...exported,content_base64:Buffer.from('bad!').toString('base64')},'html',()=>{}),/指纹校验失败/);
 await assert.rejects(f.api.saveReport(exported,'pdf',()=>{}),/文件信息不匹配/);
 await assert.rejects(f.api.saveReport(exported,'html',()=>{throw Error('已切换')}),/已切换/);
 assert.equal(f.messages.length,0);
});
test('original recovery uses the byte-preserving native action',async()=>{
 const f=fixture(),bytes=Buffer.from([239,187,191,0,255]);
 const promise=f.api.saveOriginal({filename:'原件.md',content_base64:bytes.toString('base64')},bytes);
 assert.equal(f.messages[0].action,'save_original');
 assert.deepEqual(Buffer.from(f.messages[0].base64,'base64'),bytes);
 f.emit({operation_id:f.messages[0].operation_id,status:'saved'});
 assert.equal((await promise).status,'saved');
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
test('a cancelled preparation releases controls without unlocking a newer export',async()=>{
 const f=fixture(false);let current=true,staleSignal,releaseStale,releaseCurrent;
 const stale=f.api.run(signal=>{
  staleSignal=signal;
  return new Promise(resolve=>releaseStale=resolve);
 },()=>current);
 current=false;f.api.updated();
 assert.equal(staleSignal.aborted,true);
 assert.equal(f.buttons[0].disabled,false);
 const fresh=f.api.run(()=>new Promise(resolve=>releaseCurrent=resolve));
 releaseStale();await stale;
 assert.equal(f.buttons[0].disabled,true);
 releaseCurrent();await fresh;
 assert.equal(f.buttons[0].disabled,false);
});
test('a replacement toolbar stays busy until a handed-off native save is settled',async()=>{
 const f=fixture();let current=true;
 const operation=f.api.run((_,handOff)=>{handOff();return f.api.saveText('a.html','报告')},()=>current);
 const original=f.buttons[0],replacement={disabled:false,setAttribute(){},removeAttribute(){}};
 current=false;original.isConnected=false;f.buttons.splice(0,1,replacement);
 f.api.updated();f.api.cancelPreparation();
 assert.equal(replacement.disabled,true);
 f.emit({operation_id:f.messages[0].operation_id,status:'cancelled'});
 assert.equal((await operation).status,'cancelled');
 assert.equal(replacement.disabled,false);
});

test('native completion displays the filename selected in the save panel',async()=>{
 const f=fixture();const promise=f.api.saveText('default.html','正文');
 f.emit({operation_id:f.messages[0].operation_id,status:'saved',name:'客户报告.html'});
 assert.equal((await promise).name,'客户报告.html');assert.equal(f.notices[0],'已保存：客户报告.html');
});

test('Windows save completion unlocks controls and ignores unrelated or stale messages',async()=>{
 const f=fixture(true,true),type='researchhelper_artifact_saved';
 for(const status of ['cancelled','failed','rejected','saved']){
  const done=f.api.run(()=>f.api.saveText('研究.html','正文'));
  const operation_id=f.messages.at(-1).operation_id;
  f.emitWindows({type:'another_message',operation_id,status:'saved'});
  f.emitWindows({type,status:'saved'});
  f.emitWindows({type,operation_id:'old-export',status:'saved'});
  await Promise.resolve();
  assert.equal(f.buttons[0].disabled,true);
  f.emitWindows({type,operation_id,status,name:'选定报告.html'});
  assert.equal((await done).status,status);
  assert.equal(f.buttons[0].disabled,false);
 }
 assert.deepEqual(f.notices.map(message=>message.split('：')[0]),['已取消保存','保存失败','无法保存','已保存']);
});
