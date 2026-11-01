/* Read-only report view. ctx: {esc, apiMode, reportProfile(task), reportURL(task), reportVersionBusy(task)}.
 * Uses existing workspace event handlers; never fetches data or changes frozen content.
 */
(function(global){
'use strict';
const views=global.RHDeskViews=global.RHDeskViews||{};
const previewState=`<section class="report-reader-state" id="report-reader-state" role="status" hidden><h2 data-report-reader-title></h2><p data-report-reader-message></p><button type="button" class="desk-action" data-action="retry-report" hidden>重新读取</button></section>`;
views.report=function(task,ctx){
  const {esc,apiMode,reportProfile,reportURL,reportVersionBusy}=ctx;
  const text=value=>esc(value==null?'':String(value));
  if(!task?.delivery){
    const running=task?.status==='运行中';
    const pending=Boolean(task?.pending_content||task?.has_pending_content||task?.pending_html);
    const message=pending?'报告内容已形成，尚未保存为交付版本。':running?'研究正在进行，报告生成后可在此阅读。':'在对话中继续当前研究，生成报告后可在此阅读和导出。';
    return '<section class="empty desk-report-empty"><h2>暂无研究报告</h2><p>'+message+'</p><div class="desk-report-empty-actions"><button type="button" data-action="chat">返回对话</button>'+(running?'<button type="button" class="button-secondary" data-stage="1">查看研究进展</button>':'')+'</div></section>';
  }
  const detailedAvailable=apiMode||Boolean(task.report_detailed_html);
  const profile=reportProfile(task)==='detailed'&&detailedAvailable?'detailed':'standard';
  if(!apiMode&&!(profile==='detailed'?task.report_detailed_html:task.report_html))return '<section class="empty desk-report-empty"><h2>此版本未保存报告正文</h2><p>交付记录存在，但离线文件未包含该版报告。</p></section>';
  const corrections=(Array.isArray(task.report_corrections)?task.report_corrections:[]).filter(item=>item&&item.task_id===task.id&&item.version===task.delivery.version&&typeof item.reason==='string'&&item.reason.trim());
  const profiles='<div class="report-profile-switch" role="group" aria-label="报告详略">'+['standard','detailed'].map(value=>'<button type="button" class="desk-segment" data-report-profile="'+value+'" aria-pressed="'+(profile===value)+'"'+(value==='detailed'&&!detailedAvailable?' disabled title="此离线版本未保存详细报告"':'')+'>'+(value==='standard'?'标准报告':'详细报告')+'</button>').join('')+'</div>';
  const formats='<select id="report-format" data-choice aria-label="导出格式"><option value="html">HTML</option><option value="pdf">'+(apiMode?'PDF':'打印PDF')+'</option>'+[['docx','Word'],['xlsx','Excel'],['csv','CSV']].map(([value,label])=>'<option value="'+value+'"'+(!apiMode?' disabled title="此格式需要连接本地服务"':'')+'>'+label+'</option>').join('')+'</select>';
  const more='<div class="workbench-menu" data-work-menu><button type="button" class="workbench-menu__toggle" data-work-menu-toggle aria-label="更多报告操作" aria-haspopup="menu" aria-controls="report-more-menu" aria-expanded="false"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg></button><div class="workbench-menu__items" id="report-more-menu" role="menu" hidden><button type="button" role="menuitem" tabindex="-1" data-action="print">打印报告</button><button type="button" role="menuitem" tabindex="-1" data-action="visual-library">图表方案</button><button type="button" role="menuitem" tabindex="-1" data-action="versions"'+(reportVersionBusy(task)?' disabled aria-busy="true"':'')+'>交付版本</button>'+(apiMode||corrections.length?'<button type="button" role="menuitem" tabindex="-1" data-action="report-corrections">报告更正</button>':'')+'</div></div>';
  const saved='<span class="report-save-state">版本'+text(task.delivery.version)+'</span>'+(task.synthetic?'<span class="report-case-state">合成案例</span>':'');
  const source=apiMode?'src="'+text(reportURL(task))+'"':'srcdoc="'+text((profile==='detailed'?task.report_detailed_html:task.report_html)||'')+'"';
  return '<div class="report-actions desk-report-actions">'+profiles+saved+'<div class="report-export-actions">'+formats+'<button type="button" class="desk-action desk-action--primary" data-action="export"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3v9m-3-3 3 3 3-3M4 13v4h12v-4"/></svg>导出</button>'+more+'</div></div>'+(corrections.length?'<p class="report-correction-state" role="note"><button type="button" class="text" data-action="report-corrections">查看'+corrections.length+'条报告更正</button></p>':'')+previewState+'<iframe id="report-frame" title="完整研究报告" class="report-frame" sandbox="allow-scripts allow-modals allow-same-origin" '+source+'></iframe>';
};

/* Read the already saved document. Failure never reruns research or overwrites it. */
global.RHReportReader={create(pane){
 let active=null,phase='idle',timeout=null,disposed=false,pendingFocus=null;
 function current(frame){return !!frame&&!disposed&&active===frame&&frame.isConnected&&pane.querySelector('#report-frame')===frame}
 function clear(){clearTimeout(timeout);timeout=null}
 function state(frame,next){
  if(!current(frame))return;phase=next;
  const panel=pane.querySelector('#report-reader-state');
  frame.hidden=next!=='ready';frame.setAttribute('aria-busy',String(next==='loading'));
  pane.querySelectorAll('[data-action="print"]').forEach(button=>button.disabled=next!=='ready');
  if(!panel)return;
  const focused=document.activeElement;
  const restore=next!=='loading'&&pendingFocus?.frame===frame&&(focused===document.body||focused===pendingFocus.origin||panel.contains?.(focused));
  panel.hidden=next==='ready';panel.dataset.state=next;
  panel.querySelector('[data-report-reader-title]').textContent=next==='loading'?'正在读取报告':'报告暂时无法读取';
  panel.querySelector('[data-report-reader-message]').textContent=next==='loading'?'':'已保存的交付记录保留，可重新读取。';
  panel.querySelector('[data-action="retry-report"]').hidden=next!=='error';
  if(next!=='loading')pendingFocus=null;
  if(restore&&!pane.closest?.('[hidden], [inert]'))(next==='ready'?frame:panel.querySelector('[data-action="retry-report"]')).focus({preventScroll:true});
 }
 function inspect(frame){
  try{
   const doc=frame.contentDocument;if(!doc)return 'unavailable';
   if(doc.URL==='about:blank'&&(frame.hasAttribute('src')||frame.hasAttribute('srcdoc')))return 'pending';
   if(doc.readyState!=='complete')return 'pending';
   const raw=doc.body?.textContent?.trim()||'';
   if(doc.contentType==='application/json')return 'error';
   if(raw.startsWith('{')){try{const parsed=JSON.parse(raw);if(parsed&&typeof parsed==='object'&&Object.hasOwn(parsed,'error'))return 'error'}catch{}}
   if(/^(?:Error response|Not Found|404 Not Found|Internal Server Error|500 Internal Server Error)$/i.test(doc.title.trim()))return 'error';
   if(!raw&&!doc.querySelector('img,svg,canvas'))return 'error';
   return 'ready';
  }catch{return 'unavailable'}
 }
 function loaded(event){
  const frame=event.currentTarget;if(!current(frame))return;
  const result=inspect(frame);if(result==='pending')return;
  clear();state(frame,result==='error'?'error':'ready');
 }
 function failed(event){if(current(event.currentTarget)){clear();state(event.currentTarget,'error')}}
 function start(frame){
  clear();state(frame,'loading');
  timeout=setTimeout(()=>{if(current(frame))state(frame,'error')},12000);
 }
 function detach(){clear();if(active){active.removeEventListener('load',loaded);active.removeEventListener('error',failed)}active=null;phase='idle';pendingFocus=null}
 function updated(){
  if(disposed)return;
  const frame=pane.querySelector('#report-frame');if(frame===active)return;
  detach();if(!frame)return;active=frame;
  if(!pane.querySelector('#report-reader-state')){const template=document.createElement('template');template.innerHTML=previewState;frame.before(template.content)}
  frame.addEventListener('load',loaded);frame.addEventListener('error',failed);start(frame);
  const result=inspect(frame);if(result==='ready'||result==='error'){clear();state(frame,result)}
 }
 function retry(){
  const frame=active;if(!current(frame))return;
  const panel=pane.querySelector('#report-reader-state'),origin=document.activeElement;
  pendingFocus=panel?.contains?.(origin)?{frame,origin}:null;
  start(frame);
  if(frame.hasAttribute('srcdoc'))frame.srcdoc=frame.getAttribute('srcdoc');
  else frame.src=frame.getAttribute('src');
 }
 return {updated,retry,isReady:frame=>current(frame)&&phase==='ready',destroy(){if(disposed)return;detach();disposed=true}};
}};
})(window);
