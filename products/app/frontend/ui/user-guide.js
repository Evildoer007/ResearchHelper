/* Port of OH shared/user-guide.js: same reading layout, searchable chapters and
 * focus navigation. RH-authored help is bundled locally, not fetched from OH.
 * It contains no role-private configuration, credentials or research results. */
(function(global){
  'use strict';
  const sections=[
    {id:'start',title:'发送研究问题',body:`<p>在ResChat输入研究问题并发送。可从左下角账户菜单设置研究范围与预算。</p>
      <blockquote data-example>
        <p>比较两只半导体ETF的持仓、行业暴露与主要风险。</p>
        <p>研究贵州茅台近两年的收入、利润和经营现金流，说明三者变化是否一致。</p>
        <p>核对此次降准的公告和生效日期，分析对银行板块的影响与反证。</p>
      </blockquote><p>点击输入框的加号、拖入或粘贴文件，支持PDF、TXT、Markdown、DOCX和HTML。</p>`},
    {id:'workspace',title:'ResChat与ResDesk',body:'<p>在ResChat提问、追问和阅读回答。点击报告卡打开该版本。通过左侧的ResDesk查看同一任务。</p><p>研究底稿按投资逻辑组织理论、数据、计算、反证和来源。研究概览和跟踪复核可从ResDesk上方切换。</p>'},
    {id:'evidence',title:'证据与来源',body:'<p>研究过程保留实际节点、来源、证据、计算和未解决事项。展开证据可查看字段、单位、时点、核验状态及原件位置；解析成功不等于证据核验通过。</p><p>历史时点研究检查首次公开时间，拒绝截止时点之后的信息。缺少公开时点的原件可以另建内容核对任务，回答文件写了什么，但不能据此形成历史时点投资判断。</p><p>知识与模板从账户菜单进入。通用知识用于确定核验问题，不代替本次事实；计算只使用已登记实现。缺失数据、相反证据和方法限制保留在结果中。</p>'},
    {id:'execution',title:'运行、停止与恢复',body:'<p>默认预算为100次数据请求、8次模型调用和600秒，预留1次模型调用与60秒用于交付；补证最多两轮。新任务范围中可调整有限上限。</p><p>停止或恢复不重置已经使用的预算。恢复沿用任务检查点；主动更新形成新的研究版本，不改写旧版报告。遇到保存失败，按页面状态核对结果后再重试，不把未保存内容视为交付完成。</p>'},
    {id:'delivery',title:'报告与判断追踪',body:'<p>客户报告展示与同版研究结果绑定的纸面报告，可导出HTML或打印。正文完整显示论述、图表和风险，不使用折叠依据栏；点击论点标题、证据引用或图题，在独立弹窗中阅读依据。打印不包含弹窗。完整工作底稿保留在ResDesk，旧冻结报告不自动覆盖。</p><p>图表使用已留存数据，缺少数据时展示适用表格，不生成占位曲线。判断追踪保留结论、条件、风险和版本变化；历史报告与新研究分别保留，不转换为新引擎已核验结果。报告不包含衍生品结构或产品推荐。</p>'},
    {id:'tasks',title:'任务与快捷操作',body:'<p>搜索任务使用Cmd+K，其他系统使用Ctrl+K；可按任务或报告名称查找。任务行的三点菜单支持重命名、置顶与归档。重命名只改显示标题，不改研究问题或冻结报告。</p><p>归档任务从账户菜单进入，可恢复到任务列表。运行中的研究不能归档。导入旧备份可阅读留存的原始内容。</p><p>输入框Enter发送，Shift+Enter换行；输入法选词期间不会发送。Escape关闭当前弹窗，Tab和方向键可操作菜单与说明目录。</p>'},
    {id:'offline',title:'离线与运行配置',body:'<p>独立HTML用于阅读打包结果、保存草稿和导入旧任务备份。合成案例来自固定离线材料，不是实时行情研究，也不代表真实模型质量或数据权限通过。</p><p>App的设置中心配置模型与数据源；iFinD优先，Tinyshare独立配置。已保存配置不等于供应商权限已验证，缺少数据源也不禁止本地材料研究。离线阅读使用本地资源，不请求外部字体或接口。</p><p>App、Skill与独立demo不共享生产库。关闭窗口前未发送的文件仅保留在当前窗口内存，恢复草稿后需要重新添加文件。</p>'},
  ];
  const salesSections={
    workspace:{title:'ResChat',body:'<p>在ResChat发起研究、查看研究过程和继续追问。选择左侧历史任务可返回原对话。</p>'},
    delivery:{title:'阅读报告',body:'<p>点击对话中的报告卡，在独立窗口阅读卡片标注版本的标准报告。报告库也可以打开本账户已保存的报告和历史案例。</p>'},
  };
  function createUserGuide({trigger,canOpen=()=>true,canUseDesk=()=>true,beforeOpen=()=>{},restoreFocus=origin=>origin?.focus()}={}){
    if(!trigger)throw Error('使用说明缺少入口');
    const dialog=document.createElement('dialog');
    dialog.className='user-guide';dialog.setAttribute('aria-labelledby','user-guide-title');
    // Same OH component structure; all chapter HTML below is static App-authored.
    dialog.innerHTML='<header class="user-guide__header"><div><h1 id="user-guide-title">使用说明</h1></div><button class="workspace-modal-close" type="button" data-guide-close aria-label="关闭使用说明"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button></header><div class="user-guide__layout"><aside class="user-guide__sidebar"><label for="user-guide-search">搜索说明</label><input id="user-guide-search" type="search" placeholder="例如：证据、报告、恢复" autocomplete="off"><nav aria-label="使用说明目录" data-guide-nav></nav></aside><main class="user-guide__body" tabindex="0"><p role="status" data-guide-status hidden></p><div data-guide-content></div></main></div>';
    document.body.append(dialog);
    const status=dialog.querySelector('[data-guide-status]'),content=dialog.querySelector('[data-guide-content]');
    const nav=dialog.querySelector('[data-guide-nav]'),search=dialog.querySelector('input'),body=dialog.querySelector('.user-guide__body');
    const closeButton=dialog.querySelector('[data-guide-close]');
    let chapters=[],loaded=false,destroyed=false,origin=null,restorePending=false;
    function populate(){
      if(loaded)return;
      const hasDesk=canUseDesk();
      chapters=sections.map(original=>{
        const section=hasDesk?original:{...original,...salesSections[original.id]};
        const article=document.createElement('article');article.id='guide-'+section.id;article.tabIndex=-1;
        const heading=document.createElement('h2');heading.textContent=section.title;article.append(heading);
        const text=document.createElement('div');text.innerHTML=section.body;
        text.querySelectorAll('[data-example]').forEach(example=>{
          const label=document.createElement('span');label.className='user-guide__example-label';label.textContent='可以这样提问';example.prepend(label);
        });
        article.append(text);content.append(article);
        const link=document.createElement('a');link.href='#'+article.id;link.textContent=section.title;
        link.addEventListener('click',event=>{
          event.preventDefault();article.scrollIntoView({block:'start',behavior:'instant'});article.focus({preventScroll:true});
          nav.querySelectorAll('a').forEach(item=>item.removeAttribute('aria-current'));link.setAttribute('aria-current','location');
        });nav.append(link);return {article,link};
      });loaded=true;
    }
    function filter(){
      const query=search.value.trim().toLocaleLowerCase();let found=0;
      for(const {article,link} of chapters){
        const visible=!query||article.textContent.toLocaleLowerCase().includes(query);
        article.hidden=!visible;link.hidden=!visible;link.removeAttribute('aria-current');if(visible)found++;
      }
      status.hidden=!query;status.textContent=found?'找到'+found+'个相关章节。':'没有找到相关章节，请换一个关键词。';body.scrollTop=0;
    }
    function restore(){
      if(!restorePending||destroyed||dialog.open||document.querySelector('dialog[open]'))return;
      restorePending=false;
      if(canOpen())restoreFocus(origin);
    }
    function close(){if(!dialog.open)return;dialog.close();restore()}
    function open(){
      if(destroyed||!canOpen()||dialog.open||document.querySelector('dialog[open]'))return;
      origin=document.activeElement;beforeOpen();populate();search.value='';filter();restorePending=true;
      dialog.showModal();closeButton.focus();
    }
    function keydown(event){
      // Search fields otherwise consume Escape to clear a nonempty value.
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();return}
      if(event.key!=='Tab')return;
      const controls=[...dialog.querySelectorAll('button,input,a,[tabindex="0"]')].filter(el=>!el.disabled&&!el.closest('[hidden]')&&el.getClientRects().length);
      const first=controls[0],last=controls.at(-1),active=document.activeElement;
      if(event.shiftKey&&(active===first||!controls.includes(active))){event.preventDefault();last?.focus()}
      else if(!event.shiftKey&&(active===last||!controls.includes(active))){event.preventDefault();first?.focus()}
    }
    function backdrop(event){
      if(event.target!==dialog)return;
      const rect=dialog.getBoundingClientRect();
      if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)close();
    }
    function cancel(event){event.preventDefault();close()}
    trigger.addEventListener('click',open);closeButton.addEventListener('click',close);
    dialog.addEventListener('close',restore);dialog.addEventListener('cancel',cancel);
    dialog.addEventListener('keydown',keydown);dialog.addEventListener('click',backdrop);search.addEventListener('input',filter);
    return {open,close,destroy(){
      if(destroyed)return;destroyed=true;restorePending=false;trigger.removeEventListener('click',open);
      dialog.close();dialog.remove();chapters=[];origin=null;
    }};
  }
  global.RHUserGuide={createUserGuide};
})(window);
