/* Coordinates one export through the native save result or browser download. */
(function(global){
'use strict';
const isDigest=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const entriesOf=value=>Array.isArray(value)?value:[];
const isVersion=value=>Number.isSafeInteger(value)&&value>0;
const originalExtensions=new Set(['pdf','docx','txt','md','html','htm']);
const reportMedia=new Map([
 ['html','text/html'],
 ['pdf','application/pdf'],
 ['docx','application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
 ['xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
 ['csv','text/csv'],
]);
function filename(stem,suffix){
 const safe=Array.from(stem.replace(/[\\/:*?"<>|\p{Cc}\p{Cf}]/gu,'_').trim()).slice(0,54).join('').replace(/[. ]+$/,'');
 const name=safe||'研究文件';
 return (/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name)?'_'+name:name)+suffix;
}
function reportFilename(stem,version,profile=''){
 return filename(stem,(version==null?'':'_v'+version)+(profile?'_'+profile:'')+'.html');
}
function originalFilename(name){
 if(typeof name!=='string')return null;
 const extension=/\.([a-z]+)$/i.exec(name);
 return extension&&originalExtensions.has(extension[1].toLowerCase())?
  filename(name.slice(0,extension.index),extension[0].toLowerCase()):null;
}
function recoveryEntries(backup){
 const reports=[],originals=[],seen=new Set();
 if(backup?.workspaceVersion!==4)return {reports,originals};
 const tasks=entriesOf(backup.tasks).filter(task=>typeof task?.id==='string'&&task.id&&isVersion(task.version));
 function appendReport(task,version,title,html,sha256,profile=''){
  if(typeof html!=='string'||!html.length||sha256!==undefined&&!isDigest(sha256))return;
  const label=typeof title==='string'&&title.trim()?title.trim():
   typeof task.question==='string'&&task.question.trim()?task.question.trim():'研究报告';
  const edition='版本'+version+(profile?'，'+profile:'');
  reports.push({kind:'report',label:label.slice(0,120),edition,
    filename:reportFilename(label,version,profile),html,sha256});
 }
 function appendOriginal(task,version,material,sha256,byteCount){
  if(typeof material?.name!=='string'||typeof material.original_base64!=='string'||!isDigest(sha256)
    ||byteCount!==undefined&&(!Number.isSafeInteger(byteCount)||byteCount<0))return;
  const name=originalFilename(material.name);
  if(!name)return;
  const key=JSON.stringify([task.id,version,material.name,sha256]);
  if(seen.has(key))return;
  seen.add(key);
  originals.push({kind:'original',label:name,edition:'版本'+version,filename:name,
    content_base64:material.original_base64,sha256,byteCount});
 }
 for(const task of tasks){
  const bound=entriesOf(backup.reports).filter(report=>isVersion(report?.version)&&report.version<=task.version
    &&report.content?.task_id===task.id&&report.content.research_version===report.version);
  for(const report of bound){
   const digest=report.html_sha256??(task.delivery?.version===report.version?task.delivery.html_sha256:undefined);
   appendReport(task,report.version,report.content.title,report.html,digest);
  }
  if(!bound.some(report=>report.version===task.version)){
   const digest=task.delivery?.version===task.version?task.delivery.html_sha256:undefined;
   appendReport(task,task.version,task.question,task.report_html,digest,'标准报告');
  }
  appendReport(task,task.version,task.question,task.report_detailed_html,undefined,'详细报告');
  for(const material of entriesOf(backup.originals)){
   if(material?.task_id===task.id&&isVersion(material.research_version)&&material.research_version<=task.version)
    appendOriginal(task,material.research_version,material,material.sha256,material.byte_count);
  }
  // Older backups could have an index and still keep some files only in the task.
  const records=['materials','pending_materials','source_records','pending_source_records'].flatMap(key=>entriesOf(task[key]));
  for(const record of records){
   if(record?.task_id!==task.id||!isVersion(record.research_version)||record.research_version>task.version)continue;
   const version=Object.hasOwn(record,'retained_from_version')?record.retained_from_version:record.research_version;
   if(!isVersion(version)||version>record.research_version)continue;
   const material=record.kind==='filing'?record.snapshot?.material:record.kind==='material'?record.snapshot||record:null;
   if(material)appendOriginal(task,version,material,material.hash,material.byte_count);
  }
 }
 return {reports,originals};
}
async function recoveryBytes(file){
 let bytes;
 try{
  bytes=file.kind==='report'?new TextEncoder().encode(file.html):
   Uint8Array.from(atob(file.content_base64),character=>character.charCodeAt(0));
 }catch{throw Error('档案文件的编码无效');}
 return verifyBytes(file,bytes);
}
async function verifyBytes(file,bytes,label='档案文件'){
 if(file.byteCount!==undefined&&bytes.length!==file.byteCount)throw Error(label+'长度校验失败');
 if(file.sha256!==undefined){
  if(!isDigest(file.sha256))throw Error(label+'指纹校验失败');
  const value=await crypto.subtle.digest('SHA-256',bytes);
  const actual=Array.from(new Uint8Array(value),byte=>byte.toString(16).padStart(2,'0')).join('');
  if(actual!==file.sha256)throw Error(label+'指纹校验失败');
 }
 return bytes;
}
global.RHArtifactExport={originalFilename,reportFilename,recoveryEntries,recoveryBytes,verifyBytes,create({notify}){
 let active=null,sequence=0;
 const pending=new Map();
 function complete(result){
  const id=result.operation_id||(pending.size===1?pending.keys().next().value:null);
  const operation=pending.get(id);if(!operation)return;
  pending.delete(id);
  const messages={saved:'已保存：',cancelled:'已取消保存：',failed:'保存失败：',rejected:'无法保存：'};
  notify((messages[result.status]||'保存状态未知：')+(result.error||result.name||operation.name));
  operation.resolve(result);
 }
 global.addEventListener('researchhelper:artifact-saved',event=>complete(event.detail||{}));
 global.chrome?.webview?.addEventListener('message',event=>{
  const result=event.data;
  if(result?.type==='researchhelper_artifact_saved'&&typeof result.operation_id==='string'&&result.operation_id)complete(result);
 });
 function save(message,mime,body){
  const bridge=global.webkit?.messageHandlers?.researchhelper;
  if(bridge){
   const operation_id='artifact-'+Date.now()+'-'+(++sequence);
   return new Promise((resolve,reject)=>{
    pending.set(operation_id,{resolve,name:message.name});
    try{bridge.postMessage({...message,operation_id})}catch(error){pending.delete(operation_id);reject(error)}
   });
  }
  const url=URL.createObjectURL(new Blob([body],{type:mime})),link=document.createElement('a');
  link.href=url;link.download=message.name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  notify('已开始下载：'+message.name);return Promise.resolve({status:'download_started',name:message.name});
 }
 const saveText=(name,text,mime='text/html')=>save({action:'save',name,text},mime,text);
 function saveOriginal(file,bytes){
  return save({action:'save_original',name:file.filename,base64:file.content_base64},'application/octet-stream',bytes);
 }
 async function saveBlob(action,name,blob,current){
  let base64;
  if(global.webkit?.messageHandlers?.researchhelper){
   base64=await new Promise((resolve,reject)=>{
    const reader=new FileReader();
    reader.onload=()=>resolve(reader.result.slice(reader.result.indexOf(',')+1));
    reader.onerror=()=>reject(Error('导出文件无法读取'));
    reader.onabort=reader.onerror;
    reader.readAsDataURL(blob);
   });
  }
  if(!current())throw Error('研究已切换，请重新下载当前文件');
  return save({action,name,base64},blob.type,blob);
 }
 async function saveChart(name,blob,current){
  if(typeof name!=='string'||!(blob instanceof Blob)||blob.type!=='image/png'||blob.size>20000000)
   throw Error('图表文件无效');
  const header=new Uint8Array(await blob.slice(0,8).arrayBuffer());
  if(header.join(',')!=='137,80,78,71,13,10,26,10')throw Error('图表文件无效');
  return saveBlob('save_binary',filename(name,'.png'),blob,current);
 }
 async function saveReport(result,format,assertCurrent){
  const mime=reportMedia.get(format);
  if(!mime||result?.format!==format||result.mime!==mime||
     typeof result.filename!=='string'||!result.filename.toLowerCase().endsWith('.'+format)||
     typeof result.content_base64!=='string'||!result.content_base64||!isDigest(result.sha256))
   throw Error('导出文件信息不匹配，请重新导出');
  let bytes;
  try{bytes=Uint8Array.from(atob(result.content_base64),character=>character.charCodeAt(0))}
  catch{throw Error('导出文件编码无效，请重新导出')}
  if(!bytes.length)throw Error('导出文件内容为空，请重新导出');
  await verifyBytes(result,bytes,'导出文件');
  assertCurrent();
  if(format==='html'){
   let text;
   try{text=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(bytes)}
   catch{throw Error('导出文件编码无效，请重新导出')}
   return saveText(result.filename,text,mime);
  }
  return save({action:'save_binary',name:result.filename,base64:result.content_base64},mime,bytes);
 }
 function finish(operation){
  if(active!==operation)return;
  active=null;
  for(const [button,disabled] of operation.buttons){
   button.disabled=disabled;
   button.removeAttribute('aria-busy');
  }
 }
 function cancelPreparation(){
  const operation=active;
  if(!operation||operation.handedOff)return;
  finish(operation);
  operation.controller.abort();
 }
 function updated(){
  const operation=active;
  if(!operation)return;
  if(!operation.handedOff&&!operation.current()){cancelPreparation();return}
  for(const button of operation.buttons.keys())if(button.isConnected===false)operation.buttons.delete(button);
  for(const button of document.querySelectorAll('[data-action="export"], [data-action="export-history"], [data-action="download-chat-report"]')){
   if(operation.buttons.has(button))continue;
   operation.buttons.set(button,button.disabled);
   button.disabled=true;
   button.setAttribute('aria-busy','true');
  }
 }
 async function run(work,current=()=>true){
  if(active){notify('正在导出，请稍候');return}
  const operation={controller:new AbortController(),current,handedOff:false,buttons:new Map()};
  const {signal}=operation.controller;
  active=operation;
  try{
   updated();
   if(!signal.aborted)return await work(signal,()=>{operation.handedOff=true});
  }catch(error){if(!signal.aborted)throw error}
  finally{finish(operation)}
 }
 return {saveText,saveReport,saveOriginal,saveChart,run,updated,cancelPreparation,
  saveBackup:(name,blob,current)=>saveBlob('save_backup',name,blob,current),
  saveOriginalBlob:(name,blob,current)=>saveBlob('save_original',name,blob,current)};
}};
})(window);
