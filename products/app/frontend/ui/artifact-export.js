/* Coordinates one export through the native save result or browser download. */
(function(global){
'use strict';
global.RHArtifactExport={create({notify}){
 let busy=false,sequence=0;
 const pending=new Map();
 global.addEventListener('researchhelper:artifact-saved',event=>{
  const result=event.detail||{};
  const id=result.operation_id||(pending.size===1?pending.keys().next().value:null);
  const operation=pending.get(id);if(!operation)return;
  pending.delete(id);
  const messages={saved:'已保存：',cancelled:'已取消保存：',failed:'保存失败：',rejected:'无法保存：'};
  notify((messages[result.status]||'保存状态未知：')+(result.error||result.name||operation.name));
  operation.resolve(result);
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
 function saveBinary(result){
  if(!result?.filename||typeof result.content_base64!=='string')throw Error('导出响应缺少文件内容');
  const bytes=Uint8Array.from(atob(result.content_base64),c=>c.charCodeAt(0));
  if(/\.html?$/i.test(result.filename))return saveText(result.filename,new TextDecoder('utf-8',{fatal:true}).decode(bytes),'text/html');
  return save({action:'save_binary',name:result.filename,base64:result.content_base64},result.mime||'application/octet-stream',bytes);
 }
 async function run(work){
  if(busy){notify('正在导出，请稍候');return}
  busy=true;
  const buttons=[...document.querySelectorAll('[data-action="export"], [data-action="export-history"]')].map(button=>({button,disabled:button.disabled}));
  buttons.forEach(({button})=>{button.disabled=true;button.setAttribute('aria-busy','true')});
  try{return await work()}finally{busy=false;buttons.forEach(({button,disabled})=>{button.disabled=disabled;button.removeAttribute('aria-busy')})}
 }
 return {saveText,saveBinary,run};
}};
})(window);
