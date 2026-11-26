/* Read-only report view. The displayed edition may predate the active research.
 * Uses existing workspace event handlers; never fetches data or changes frozen content.
 */
(function(global){
'use strict';
const views=global.RHDeskViews=global.RHDeskViews||{};
const previewState=`<section class="report-reader-state" id="report-reader-state" role="status" hidden><h2 data-report-reader-title></h2><p data-report-reader-message></p><button type="button" class="button-secondary desk-action" data-action="retry-report" hidden>重新读取</button></section>`;
views.report=function(task,ctx){
  const {esc,apiMode,reportProfile,reportURL,reportVersionBusy=()=>false,taskActions=()=>'',edition=task?.delivery,latest=edition,packagedReplacement}=ctx;
  const text=value=>esc(value==null?'':String(value));
  const running=task?.status==='运行中';
  const settling=task?.batch_settling===true;
  const checkpoint=global.RHResearchData.reportCheckpoint(task);
  const blocked=apiMode&&!settling?task?.recovery_block?.reason:null;
  const stopped=!settling&&['失败','已取消'].includes(task?.status);
  const canUseData=stopped&&task.can_finish_from_data===true&&latest?.version!==task.version;
  const recovery=apiMode&&(checkpoint||canUseData)&&!blocked?taskActions(task,{variant:edition?'secondary':'primary'}):'';
  const versionButton='<button type="button" class="button-secondary desk-action" data-action="versions"'+(reportVersionBusy(task)?' disabled aria-busy="true"':'')+'>交付版本</button>';
  if(!edition){
    const heading=checkpoint?(blocked?'报告尚未交付':global.RHResearchData.taskStatus(task).label):'暂无研究报告';
    const message=blocked?text(blocked):settling?'本批次正在收尾。':checkpoint&&!apiMode?'可在ResearchHelper中继续出稿。':
      checkpoint==='submission'?'可用已留存的内容生成报告。':checkpoint?'本版正文已留存。':
      running?'研究正在进行，报告生成后可在此阅读。':canUseData?'可用已留存的资料出稿。':'在对话中继续当前研究，生成报告后可在此阅读和导出。';
    const versions=apiMode&&task?.update_from?versionButton:'';
    return '<section class="empty desk-report-empty"><h2>'+heading+'</h2><p>'+message+'</p><div class="desk-report-empty-actions">'+recovery+versions+'<button type="button"'+(recovery?' class="button-secondary"':'')+' data-action="chat">返回对话</button>'+(running?'<button type="button" class="button-secondary" data-stage="1">查看研究进展</button>':'')+'</div></section>';
  }
  const older=edition.version<task.version;
  const status=latest?.version===task.version?'':blocked?'尚未交付。'+blocked:settling?'正在收尾':running?'正在更新':
    checkpoint==='content'?'待保存':checkpoint==='submission'?'原文已留存':task.status==='失败'?'更新失败':task.status==='已取消'?'已取消更新':'尚未保存';
  const latestButton=latest&&latest.version!==edition.version?'<button type="button" class="button-secondary desk-action" data-action="latest-report">阅读版本'+text(latest.version)+'</button>':'';
  const revision=older?'<div class="report-revision-state"><p role="status">阅读版本'+text(edition.version)+(status?'，版本'+text(task.version)+text(status):'')+'</p>'+recovery+latestButton+'</div>':'';
  const detailedAvailable=apiMode||Boolean(task.report_detailed_html);
  const profile=reportProfile(task,edition.version);
  if(!apiMode&&!(profile==='detailed'?task.report_detailed_html:task.report_html)){
    const replacement=packagedReplacement?'<div class="desk-report-empty-actions"><button type="button" class="button-secondary desk-action" data-task="'+text(packagedReplacement.id)+'" data-task-report="'+text(packagedReplacement.delivery?.version)+'">打开当前样例</button></div>':'';
    return '<section class="empty desk-report-empty"><h2>此版本未保存报告正文</h2>'+replacement+'</section>';
  }
  const notes=edition.corrections??task.report_corrections;
  const corrections=(Array.isArray(notes)?notes:[]).filter(item=>item&&item.task_id===task.id&&item.version===edition.version&&typeof item.reason==='string'&&item.reason.trim());
  const profiles='<div class="report-profile-switch" role="group" aria-label="报告详略">'+['standard','detailed'].map(value=>'<button type="button" class="desk-segment" data-report-profile="'+value+'" aria-pressed="'+(profile===value)+'"'+(value==='detailed'&&!detailedAvailable?' disabled title="此离线版本未保存详细报告"':'')+'>'+(value==='standard'?'标准报告':'详细报告')+'</button>').join('')+'</div>';
  const formats='<select id="report-format" data-choice aria-label="导出格式"><option value="html">HTML</option><option value="pdf">'+(apiMode?'PDF':'打印PDF')+'</option>'+[['docx','Word'],['xlsx','Excel'],['csv','CSV']].map(([value,label])=>'<option value="'+value+'"'+(!apiMode?' disabled title="此格式需要连接本地服务"':'')+'>'+label+'</option>').join('')+'</select>';
  const more='<div class="workbench-menu" data-work-menu><button type="button" class="workbench-menu__toggle" data-work-menu-toggle aria-label="更多报告操作" aria-haspopup="menu" aria-controls="report-more-menu" aria-expanded="false"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg></button><div class="workbench-menu__items" id="report-more-menu" role="menu" hidden><button type="button" role="menuitem" tabindex="-1" data-action="print">打印报告</button><button type="button" role="menuitem" tabindex="-1" data-action="visual-library">图表方案</button><button type="button" role="menuitem" tabindex="-1" data-action="backup">任务备份</button><button type="button" role="menuitem" tabindex="-1" data-action="versions"'+(reportVersionBusy(task)?' disabled aria-busy="true"':'')+'>交付版本</button>'+(apiMode||corrections.length?'<button type="button" role="menuitem" tabindex="-1" data-action="report-corrections">报告更正</button>':'')+'</div></div>';
  const saved=(older?'':'<span class="report-save-state">版本'+text(edition.version)+'</span>')+(task.synthetic?'<span class="report-case-state">合成案例</span>':'');
  const source=apiMode?'src="'+text(reportURL(task,edition.version))+'" data-report-digest="'+text(edition.digest)+'"':'srcdoc="'+text((profile==='detailed'?task.report_detailed_html:task.report_html)||'')+'"';
  const reading=JSON.stringify(['report-document',task.id,edition.version,edition.digest,profile]);
  return '<div class="report-actions desk-report-actions">'+profiles+saved+'<div class="report-export-actions">'+formats+'<button type="button" class="button-primary desk-action" data-action="export"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3v9m-3-3 3 3 3-3M4 13v4h12v-4"/></svg>导出</button>'+more+'</div></div>'+revision+(corrections.length?'<p class="report-correction-state" role="note"><button type="button" class="text" data-action="report-corrections">查看'+corrections.length+'条报告更正</button></p>':'')+previewState+'<iframe id="report-frame" title="完整研究报告，版本'+text(edition.version)+'" class="report-frame" data-reading-key="'+text(reading)+'" sandbox="allow-scripts allow-modals allow-same-origin" '+source+'></iframe>';
};

/* Read the already saved document. Failure never reruns research or overwrites it. */
global.RHReportReader={create(pane,{onAuthenticationRequired=()=>{},onReady=()=>{},
 readPosition=()=>null,writePosition=()=>{},onScroll=()=>{}}={}){
 let active=null,phase='idle',timeout=null,disposed=false,pendingFocus=null;
 let reading=null,scrollTimer=null;
 function current(frame){return !!frame&&!disposed&&active===frame&&frame.isConnected&&pane.querySelector('#report-frame')===frame}
 function clear(){clearTimeout(timeout);timeout=null}
 function syncPrintControls(){pane.querySelectorAll('[data-action="print"]').forEach(button=>button.disabled=phase!=='ready')}
 function state(frame,next){
  if(!current(frame))return;phase=next;
  const panel=pane.querySelector('#report-reader-state');
  frame.hidden=next!=='ready';frame.setAttribute('aria-busy',String(next==='loading'));
  syncPrintControls();
  if(!panel)return;
  const focused=document.activeElement;
  const restore=next!=='loading'&&pendingFocus?.frame===frame&&(focused===document.body||focused===pendingFocus.origin||panel.contains?.(focused));
  panel.hidden=next==='ready';panel.dataset.state=next;
  panel.querySelector('[data-report-reader-title]').textContent=next==='loading'?'正在读取报告':'报告暂时无法读取';
  panel.querySelector('[data-report-reader-message]').textContent=next==='loading'?'':'可导出已保存的报告。';
  panel.querySelector('[data-action="retry-report"]').hidden=next!=='error';
  if(next!=='loading')pendingFocus=null;
  if(restore&&!pane.closest?.('[hidden], [inert]'))(next==='ready'?frame:panel.querySelector('[data-action="retry-report"]')).focus({preventScroll:true});
 }
 function reportURL(value){
  try {
   if(!value)return null;
   const url=new URL(value,document.baseURI);
   url.hash='';
   return url.href;
  } catch {return null}
 }
 function localExample(frame){
  const source=frame.getAttribute('src'),key=/^reports\/([a-z0-9-]+)\.html$/.exec(source||'')?.[1];
  return window.location.protocol==='file:'&&key?{key,url:reportURL(source)}:null;
 }
 function matchesEdition(doc,profile,digest){
  if(!/^[a-f0-9]{64}$/.test(digest||'')||doc.body?.dataset?.reportVariant!==profile)return false;
  try{
   const paper=JSON.parse(doc.querySelector('script#rh-publication-document[type="application/json"]')?.textContent);
   return paper?.schema==='researchhelper.presentation-document'&&paper.version===1&&
     paper.profile===profile&&paper.content_hash===digest;
  }catch{return false}
 }
 function inspect(frame){
  try{
   const doc=frame.contentDocument;if(!doc)return 'unavailable';
   if(doc.URL==='about:blank'&&(frame.hasAttribute('src')||frame.hasAttribute('srcdoc')))return 'pending';
   if(doc.readyState!=='complete')return 'pending';
   const source=frame.getAttribute('src'),expected=reportURL(source);
   if(source&&(!expected||reportURL(doc.URL)!==expected))return 'error';
   const raw=doc.body?.textContent?.trim()||'',json=doc.contentType==='application/json';
   if(json||raw.startsWith('{')){
    try{
     const payload=JSON.parse(raw);
     if(json&&payload?.failure_code==='authentication_required'&&source&&
        new URL(doc.URL).origin===window.location.origin&&window.location.protocol!=='file:')return 'authentication';
     if(payload&&typeof payload==='object'&&Object.hasOwn(payload,'error'))return 'error';
    }catch{}
   }
   if(doc.contentType!=='text/html')return 'error';
   if(expected){
    const url=new URL(expected),example=/\/reports\/([a-z0-9-]+)\.html$/.exec(url.pathname);
    const edition=example
      ?doc.querySelector('meta[name="rh-source-key"]')?.content===example[1]
      :matchesEdition(doc,url.searchParams.get('profile')||'standard',frame.getAttribute('data-report-digest'));
    if(!edition||!doc.querySelector('main')||!doc.querySelector('h1'))return 'error';
   }
   if(/^(?:Error response|Not Found|404 Not Found|Internal Server Error|500 Internal Server Error)$/i.test(doc.title.trim()))return 'error';
   if(!raw&&!doc.querySelector('img,svg,canvas'))return 'error';
   return 'ready';
  }catch{return 'unavailable'}
 }
 function visible(frame){return current(frame)&&phase==='ready'&&!frame.closest('[hidden], [inert]')}
 function capture(){
  if(!reading||!visible(reading.frame)||!reading.restored)return;
  const {scroller,key}=reading;
  const maximum=Math.max(0,scroller.scrollHeight-scroller.clientHeight);
  if(maximum===0||scroller.clientHeight===0)return;
  const top=Math.max(0,Math.min(maximum,scroller.scrollTop));
  writePosition(key,{top,ratio:top/maximum,atBottom:maximum-top<=2,width:scroller.clientWidth,height:scroller.clientHeight});
 }
 function restore(){
  if(!reading||reading.restored||!visible(reading.frame))return;
  const {scroller,key}=reading,saved=readPosition(key);
  // Measuring the revealed report flushes layout even when WebKit pauses animations.
  const height=scroller.clientHeight;
  if(height===0)return;
  const maximum=Math.max(0,scroller.scrollHeight-height);
  if(saved&&Number.isFinite(saved.top)&&saved.top>=0&&Number.isFinite(saved.ratio)&&saved.ratio>=0&&saved.ratio<=1){
   if(maximum===0&&saved.top>0)return;
   scroller.scrollTop=saved.atBottom?maximum:saved.width&&Math.abs(saved.width-scroller.clientWidth)>1
     ?saved.ratio*maximum:saved.top;
  }
  reading.restored=true;
 }
 function scrolled(){
  if(!reading?.restored||!visible(reading.frame))return;
  clearTimeout(scrollTimer);
  scrollTimer=setTimeout(()=>{if(reading&&visible(reading.frame))onScroll()},180);
 }
 function stopReading(){
  clearTimeout(scrollTimer);scrollTimer=null;
  reading?.doc.removeEventListener('scroll',scrolled);reading=null;
 }
 function track(frame){
  stopReading();
  const key=frame.getAttribute('data-reading-key');
  if(!key)return;
  try{
   const doc=frame.contentDocument,scroller=doc?.scrollingElement;
   if(!scroller)return;
   reading={frame,doc,scroller,key,restored:false};
   doc.addEventListener('scroll',scrolled,{passive:true});
   restore();
  }catch{/* The saved report can be read without a same-origin scroll position. */}
 }
 function finish(frame,result){
  clear();
  if(result==='ready'&&current(frame))onReady(frame);
  state(frame,result==='ready'?'ready':'error');
  if(result==='ready'&&current(frame))track(frame);
  else stopReading();
  if(result==='authentication')onAuthenticationRequired();
 }
 function loaded(event){
  const frame=event.currentTarget;if(!current(frame)||phase==='error')return;
  const result=inspect(frame);if(result==='pending')return;
  const local=result==='unavailable'&&localExample(frame);
  if(local){
   // A file-origin iframe can finish loading while its document remains
   // opaque. Only the expected report may confirm that its body exists.
   try{frame.contentWindow?.postMessage({type:'rh:check-local-report',key:local.key},'*')}catch{}
   return;
  }
  finish(frame,result);
 }
 function receive(event){
  const frame=active;
  if(!current(frame)||phase!=='loading'||event.source!==frame.contentWindow||event.origin!=='null'||
     event.data?.type!=='rh:local-report-ready')return;
  const local=localExample(frame);
  if(local?.url&&event.data.key===local.key&&reportURL(event.data.href)===local.url)finish(frame,'ready');
 }
 window.addEventListener('message',receive);
 function failed(event){if(current(event.currentTarget)&&phase!=='error')finish(event.currentTarget,'error')}
 function start(frame){
  clear();state(frame,'loading');
  timeout=setTimeout(()=>{if(current(frame))finish(frame,'error')},10000);
 }
 function detach(){clear();stopReading();if(active){active.removeEventListener('load',loaded);active.removeEventListener('error',failed)}active=null;phase='idle';pendingFocus=null}
 function updated(retryOrigin=null){
  if(disposed)return;
  const frame=pane.querySelector('#report-frame');
  if(frame===active){if(frame){syncPrintControls();restore()}return}
  detach();if(!frame)return;active=frame;
  if(!pane.querySelector('#report-reader-state')){const template=document.createElement('template');template.innerHTML=previewState;frame.before(template.content)}
  if(retryOrigin)pendingFocus={frame,origin:retryOrigin};
  frame.addEventListener('load',loaded);frame.addEventListener('error',failed);start(frame);
  const result=inspect(frame);if(result!=='pending'&&result!=='unavailable')finish(frame,result);
 }
 function retry(){
  const frame=active;if(!current(frame)||phase!=='error')return;
  const panel=pane.querySelector('#report-reader-state'),origin=document.activeElement;
  frame.replaceWith(frame.cloneNode(false));
  updated(panel?.contains?.(origin)?origin:null);
 }
 function print(frame){
  const local=localExample(frame);
  if(local&&!frame.contentDocument){
   frame.contentWindow.postMessage({type:'rh:print-local-report',key:local.key},'*');
   return;
  }
  frame.contentWindow.print();
 }
 return {updated,retry,print,capture,isReady:frame=>current(frame)&&phase==='ready',destroy(){
  if(disposed)return;
  detach();window.removeEventListener('message',receive);disposed=true;
 }};
}};
})(window);
