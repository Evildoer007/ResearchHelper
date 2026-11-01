/* Independent RH configuration UI inside the OH-derived settings shell.
 * Saving configuration is never a model call, data request or research run. */
(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const params = new URLSearchParams(location.search);
  const embedded = parent !== window && params.get('embedded') === '1';
  const apiMode = location.protocol === 'http:';
  const instance = params.get('instance'), expectedOwner = params.get('owner');
  let boot = {}, token = '', invalidated = false, saving = false;
  const send = (type, fields = {}) => {
    if (embedded) parent.postMessage({type:'researchhelper.settings-' + type,instance,...fields}, location.protocol === 'file:' ? '*' : location.origin);
  };
  const manager = () => apiMode && (!boot.authentication || boot.role === 'admin');
  const live = () => boot.execution?.mode ? boot.execution.mode==='live' : boot.execution?.enabled===true;
  function invalidate() {
    if (invalidated) return;
    invalidated = true;
    document.querySelectorAll('input[type=password]').forEach(input => input.value = '');
    $('#settings-content').replaceChildren();$('#settings-status').textContent = '登录已失效，请重新登录';
    send('invalidated');
  }
  async function request(route, body) {
    if (invalidated) throw Error('登录状态已改变');
    const controller = body ? null : new AbortController();
    const timer = controller ? setTimeout(() => controller.abort(),10000) : null;
    try {
      const response = await fetch(route,{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',
        signal:controller?.signal,headers:body?{'Content-Type':'application/json','X-ResearchHelper-Token':token}:{},body:body?JSON.stringify(body):undefined});
      if (response.status === 401 || response.status === 403) { invalidate();throw Error('登录或权限已失效'); }
      let result;
      try { result = await response.json(); } catch { throw Error(body?'保存结果未确认，请重新打开设置核对':'本地响应无法读取，请重新连接'); }
      if (!response.ok) throw Error(result.error || '本地请求失败');
      if (invalidated) throw Error('登录状态已改变');
      return result;
    } catch (error) {
      if (error.name === 'AbortError') throw Error('读取超时，请重新连接');
      throw error;
    } finally { if(timer !== null)clearTimeout(timer); }
  }
  function adopt(value) {
    if ((expectedOwner && value.account !== expectedOwner) ||
        (boot.authentication && (value.account !== boot.account || value.role !== boot.role))) {
      invalidate();throw Error('账户已改变，请重新打开设置');
    }
    boot = value;token = value.token || token;
  }
  const field = (id,label,value = '',type = 'text',extra = '') =>
    '<div class="field"><label for="'+id+'">'+label+'</label><input id="'+id+'" type="'+type+'" value="'+escape(value)+'" '+extra+'></div>';
  const secret = (id,label,saved) => field(id,label,'','password','autocomplete="new-password" placeholder="'+(saved?'已保存，留空保留':'尚未保存')+'"') +
    '<label class="settings-check"><input id="clear-'+id+'" type="checkbox" '+(saved?'':'disabled')+'>清除已保存的'+label+'</label>';
  const foot = title => '<div class="form-foot"><p class="form-result" role="status" aria-live="polite"></p><button class="button-primary" type="submit">'+title+'</button></div>';
  function modelPreset(model) {
    try {
      const url = new URL(model.endpoint);
      if (url.protocol==='https:' && url.hostname==='api.deepseek.com' && (!url.port||url.port==='443') && !url.username && !url.password && !url.search && !url.hash &&
          ['','/','/v1','/v1/','/chat/completions','/v1/chat/completions'].includes(url.pathname)) {
        if(model.model==='deepseek-flash')return 'deepseek-flash';
        if(model.model==='deepseek-v4-pro')return 'deepseek-v4-pro';
      }
    } catch {}
    return 'custom';
  }
  function formHTML(name) {
    const model = boot.model_settings || {}, sources = boot.source_settings || {};
    if (name === 'model-form') return '<form class="setup-form" id="model-form"><div class="model-form-fields">'+
      '<div class="field"><label for="model-preset">服务商与模型</label><select data-choice id="model-preset">'+
      [['custom','自定义兼容服务'],['deepseek-flash','DeepSeek Flash'],['deepseek-v4-pro','DeepSeek Pro']].map(([value,label])=>'<option value="'+value+'" '+(modelPreset(model)===value?'selected':'')+'>'+label+'</option>').join('')+'</select></div>'+
      field('model-endpoint','服务地址',model.endpoint,'url','required placeholder="https://api.deepseek.com"')+
      field('model-name','模型名称',model.model,'text','required')+secret('model-key','API密钥',model.has_key)+
      '<div class="model-effort-row"><div class="field"><label for="model-reasoning">默认推理强度</label><select data-choice id="model-reasoning">'+
      [['default','服务默认'],['none','关闭思考'],['low','低'],['high','高'],['max','最高']].map(([value,label])=>'<option value="'+value+'" '+((model.reasoning_effort||'default')===value?'selected':'')+'>'+label+'</option>').join('')+'</select></div>'+
      '<div class="field"><label for="model-planning">规划推理强度</label><select data-choice id="model-planning">'+
      [['inherit','沿用默认强度'],['none','关闭思考'],['low','低'],['high','高'],['max','最高']].map(([value,label])=>'<option value="'+value+'" '+((model.planning_effort||'inherit')===value?'selected':'')+'>'+label+'</option>').join('')+'</select></div></div>'+
      '<div class="field"><label for="model-extraction">原文提取方式</label><select data-choice id="model-extraction"><option value="default" '+(model.extraction_mode!=='direct'?'selected':'')+'>沿用默认强度</option><option value="direct" '+(model.extraction_mode==='direct'?'selected':'')+'>直接提取，不启用思考</option></select></div></div>'+foot('保存模型配置')+'</form>';
    if (name === 'data-form') return '<form class="setup-form" id="data-form"><h3>iFinD</h3><p class="field-help">Access Token或Refresh Token任选一项。</p>'+
      secret('ifind-access-token','Access Token',sources.ifind?.has_access_token)+secret('ifind-refresh-token','Refresh Token',sources.ifind?.has_refresh_token)+
      '<h3>Tinyshare</h3>'+secret('tinyshare-token','Tinyshare Token',sources.tinyshare?.has_token)+foot('保存数据源配置')+'</form>';
    return '<form class="setup-form" id="execution-form"><p class="field-help">在线研究会将任务材料发送到配置的模型服务。</p>'+
      '<label class="settings-check"><input id="execution-enabled" type="checkbox" '+(live()?'checked':'')+'>启用在线研究</label>'+
      '<label class="settings-check"><input id="execution-confirm" type="checkbox" required>确认应用此运行模式</label>'+foot('应用运行模式')+'</form>';
  }
  function statusHTML() {
    const readiness = boot.research_readiness || {}, model = boot.model_settings || {}, sources = boot.source_settings || {};
    const rows = [['运行模式',live()?'在线研究':'离线研究'],['模型',manager()?(model.has_key&&model.endpoint&&model.model?'已保存，连接未验证':'未配置'):(readiness.model_configured===true?'已保存配置':'由admin管理')]];
    const dataStatus = configured => configured ? (readiness.data_credentials_available===false?'凭据需重新保存':'已保存配置') : '未配置';
    if (manager()) rows.push(['iFinD',dataStatus(sources.ifind?.has_access_token||sources.ifind?.has_refresh_token)],['Tinyshare',dataStatus(sources.tinyshare?.has_token)]);
    return '<dl class="runtime-status-list">'+rows.map(([label,value])=>'<div><dt>'+label+'</dt><dd><strong>'+value+'</strong></dd></div>').join('')+'</dl>';
  }
  function capabilitiesHTML() {
    const names = {ifind:'iFinD',tinyshare:'Tinyshare',wind:'Wind',material:'本地材料'};
    return '<details class="source-capabilities"><summary>已登记数据能力</summary>'+Object.entries(boot.capabilities || {}).map(([key,value]) =>
      '<h3>'+escape(names[key] || key)+'</h3><p>'+escape(value.state)+'</p>'+(value.registered_operations?.length?
        '<ul>'+value.registered_operations.map(item=>'<li>'+escape(item.operation)+(item.raw_only?'，仅原始记录':'')+'</li>').join('')+'</ul>':
        '<p>'+escape(value.formats?.join('、') || '尚无已登记接口')+'</p>')).join('')+'</details>';
  }
  // OH appearance glyphs, with independent ResearchHelper preference handling.
  const appearanceIcons = {
    light: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M11.3496 8C11.3496 6.14985 9.85015 4.65039 8 4.65039C6.14985 4.65039 4.65039 6.14985 4.65039 8C4.65039 9.85015 6.14985 11.3496 8 11.3496C9.85015 11.3496 11.3496 9.85015 11.3496 8ZM12.6504 8C12.6504 10.5681 10.5681 12.6504 8 12.6504C5.43188 12.6504 3.34961 10.5681 3.34961 8C3.34961 5.43188 5.43188 3.34961 8 3.34961C10.5681 3.34961 12.6504 5.43188 12.6504 8Z" fill="currentColor"/><path d="M8.65039 0.5V2.5H7.34961V0.5H8.65039ZM8.65039 13.5V15.5H7.34961V13.5H8.65039ZM3.15808 2.24035L4.57229 3.65456L3.6525 4.57435L2.23829 3.16014L3.15808 2.24035ZM12.3505 11.4327L13.7647 12.8469L12.8449 13.7667L11.4307 12.3525L12.3505 11.4327ZM2.24537 12.8469L3.65958 11.4327L4.57937 12.3525L3.16516 13.7667L2.24537 12.8469ZM11.4377 3.65455L12.852 2.24033L13.7718 3.16012L12.3575 4.57434L11.4377 3.65455ZM0.5 7.35461H2.5V8.6554H0.5V7.35461ZM13.5 7.35461H15.5V8.6554H13.5V7.35461Z" fill="currentColor"/></svg>',
    dark: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M13.2764 9.52324C12.5607 9.97754 11.7177 10.242 10.7812 10.242C8.11386 10.2419 5.95042 8.07997 5.9502 5.41289C5.9502 4.48128 6.21453 3.61071 6.67188 2.87285C4.30332 3.4658 2.54992 5.60845 2.5498 8.16093C2.5498 11.1712 4.99103 13.6102 8 13.6102C10.5383 13.6102 12.6709 11.8724 13.2764 9.52324ZM7.05078 5.41289C7.051 7.47224 8.72116 9.1423 10.7812 9.14238C11.9248 9.14238 12.887 8.63397 13.5781 7.8084C13.7266 7.63106 13.9701 7.56547 14.1875 7.64433C14.4049 7.72329 14.5497 7.9297 14.5498 8.16093C14.5498 11.7766 11.6161 14.7098 8 14.7098C4.38402 14.7098 1.4502 11.7792 1.4502 8.16093C1.45033 4.54322 4.3812 1.61015 8 1.61015C8.23027 1.61015 8.43585 1.75352 8.51562 1.96953C8.59536 2.18554 8.53241 2.42829 8.35742 2.57793C7.55573 3.26311 7.05078 4.27876 7.05078 5.41289Z" fill="currentColor"/></svg>',
    auto: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M12.1665 13.5811V14.7803H3.66651V13.5811H12.1665Z" fill="currentColor"/><path d="M13.4453 7.02379C13.4453 6.04702 13.4452 5.3616 13.3887 4.83434C13.3333 4.31828 13.2302 4.02378 13.0723 3.80309C12.9446 3.62475 12.7877 3.46883 12.6094 3.34117C12.3887 3.18328 12.0942 3.08007 11.5781 3.02477C11.0508 2.96829 10.3655 2.96715 9.38867 2.96715H6.61035C5.63359 2.96715 4.94816 2.96827 4.4209 3.02477C3.90486 3.0801 3.61034 3.18321 3.38965 3.34117C3.21143 3.46878 3.05534 3.62487 2.92774 3.80309C2.76977 4.02377 2.66667 4.3183 2.61133 4.83434C2.55483 5.3616 2.55371 6.04702 2.55371 7.02379C2.55371 8.0006 2.55485 8.68596 2.61133 9.21324C2.66663 9.72936 2.76983 10.0238 2.92774 10.2445C3.0554 10.4228 3.21131 10.5797 3.38965 10.7074C3.61034 10.8654 3.90484 10.9685 4.4209 11.0238C4.94816 11.0803 5.63359 11.0804 6.61035 11.0804H9.38867C10.3654 11.0804 11.0508 11.0803 11.5781 11.0238C12.0941 10.9685 12.3887 10.8652 12.6094 10.7074C12.7877 10.5797 12.9446 10.4229 13.0723 10.2445C13.2301 10.0238 13.3334 9.72927 13.3887 9.21324C13.4452 8.68596 13.4453 8.00058 13.4453 7.02379ZM14.6455 7.02379C14.6455 7.97428 14.646 8.73509 14.5811 9.34117C14.5149 9.95828 14.3756 10.4858 14.0479 10.9437C13.8436 11.229 13.5938 11.4788 13.3086 11.683C12.8507 12.0108 12.3232 12.15 11.7061 12.2162C11.1 12.2811 10.3391 12.2806 9.38867 12.2806H6.61035C5.66018 12.2806 4.89991 12.2811 4.29395 12.2162C3.67684 12.15 3.14935 12.0108 2.69141 11.683C2.40613 11.4788 2.15639 11.229 1.95215 10.9437C1.62436 10.4858 1.4841 9.95828 1.41797 9.34117C1.35305 8.73511 1.35449 7.97424 1.35449 7.02379C1.35449 6.07366 1.35308 5.31333 1.41797 4.70738C1.4841 4.09028 1.62436 3.56279 1.95215 3.10485C2.15638 2.81956 2.40613 2.56982 2.69141 2.36559C3.14935 2.03779 3.67684 1.89753 4.29395 1.83141C4.8999 1.76652 5.66022 1.76793 6.61035 1.76793H9.38867C10.3391 1.76793 11.1 1.76649 11.7061 1.83141C12.3232 1.89753 12.8507 2.03779 13.3086 2.36559C13.5939 2.56982 13.8436 2.81957 14.0479 3.10485C14.3756 3.56279 14.5149 4.09028 14.5811 4.70738C14.646 5.31335 14.6455 6.07362 14.6455 7.02379Z" fill="currentColor"/></svg>',
  };
  function render() {
    const section = (id,title,content) => '<section id="'+id+'" class="setting-section" hidden><h2 tabindex="-1">'+title+'</h2>'+content+'</section>';
    const appearance = '<div class="optionhelper-general-section"><div class="optionhelper-appearance-row"><div class="optionhelper-general-title">外观</div><div class="optionhelper-theme-cubes" role="group" aria-label="外观">'+
      [['light','浅色'],['dark','深色'],['auto','跟随系统']].map(([value,label])=>'<button class="optionhelper-theme-cube" type="button" data-theme-set="'+value+'" aria-pressed="false">'+appearanceIcons[value]+label+'</button>').join('')+'</div></div>'+ (embedded?'<div class="optionhelper-scale-row"><div class="optionhelper-general-title" id="scale-label">界面缩放</div><select id="interface-scale" data-choice aria-labelledby="scale-label" aria-label="界面缩放比例">'+[.8,.9,1,1.1,1.25,1.4].map(scale=>'<option value="'+scale+'" '+(scale===(Number(params.get('scale'))||1)?'selected':'')+'>'+Math.round(scale*100)+'%</option>').join('')+'</select></div>':'')+'</div>';
    const account = boot.authentication ? '<dl class="runtime-status-list"><div><dt>当前账户</dt><dd><strong>'+escape(boot.account)+'</strong><span>'+ (boot.role==='admin'?'管理员':'研究账户')+'</span></dd></div></dl>'+
      (embedded?'<div class="settings-actions"><button type="button" class="button-quiet" data-workspace-action="switch-account">切换账户</button><button type="button" class="button-quiet" data-workspace-action="logout">退出登录</button></div>':'')+
      (manager()?'<form id="sales-password-form" class="setup-form">'+field('sales-new-password','sales新密码','','password','autocomplete="new-password" minlength="8" maxlength="4096" required')+
        field('sales-password-confirm','确认新密码','','password','autocomplete="new-password" minlength="8" maxlength="4096" required')+'<p class="field-help">重置后sales须重新登录，研究档案保留。</p>'+foot('重置sales密码')+'</form>'+ (boot.legacy_unassigned_count?
        '<details><summary>认领旧档案</summary><form id="claim-legacy-form" class="setup-form"><p>'+Number(boot.legacy_unassigned_count)+'项旧任务尚未分配账户。</p><label class="settings-check"><input id="claim-confirm" type="checkbox" required>确认将旧档案归入admin</label>'+foot('确认认领')+'</form></details>':''):''):'<p>独立HTML不管理App账户。</p>';
    const storage = '<div class="storage-location"><strong>研究档案位置</strong><p>'+escape(boot.storage_location || (apiMode?'当前研究服务的独立档案目录':'本浏览器的离线任务记录'))+'</p><p class="field-help">App与Skill档案独立，不共享凭据或生产库。</p></div>'+
      (embedded?'<div class="settings-actions"><button type="button" class="button-quiet" data-workspace-action="cases">合成案例</button><button type="button" class="button-quiet" data-workspace-action="import">导入任务备份</button><button type="button" class="button-quiet" data-workspace-action="knowledge">知识与模板</button></div>':'<a class="button-quiet" href="../index.html">返回研究工作区</a>');
    $('#settings-content').innerHTML = section('preferences','通用设置',appearance)+
      section('runtime','运行状态','<div id="runtime-status">'+statusHTML()+'</div><p class="field-help">已保存配置不代表接口权限已验证。</p>'+ (manager()?(boot.execution?.configurable?formHTML('execution-form'):'<p>当前运行策略锁定，不能在此开启真实调用。</p>'):'<p>模型、数据源与运行模式由admin管理。</p>'))+
      (manager()?section('model','模型配置',formHTML('model-form'))+section('data','数据接口',formHTML('data-form')+capabilitiesHTML()):'')+
      section('account','账户管理',account)+section('storage','存储与导出',storage);
    document.querySelectorAll('[data-admin-only]').forEach(link=>link.hidden=!manager());
    window.RHChoice.enhanceSelects(document);syncTheme();selectSection(location.hash.slice(1));
  }
  function selectSection(requested, focus = false) {
    const aliases = {'model-endpoint':'model','ifind-access-token':'data','tinyshare-token':'data','execution-enabled':'runtime'};
    let id = aliases[requested] || requested || 'preferences';
    if (!document.getElementById(id)?.classList.contains('setting-section')) id = 'preferences';
    document.documentElement.dataset.settingsSection=id;
    document.querySelectorAll('.setting-section').forEach(section=>section.hidden=section.id!==id);
    document.querySelectorAll('.settings-nav a').forEach(link=>{
      if(link.hash==='#'+id)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
    });
    if (focus) document.querySelector('#'+id+' h2')?.focus({preventScroll:true});
    $('.settings-options').scrollTop=0;
  }
  function syncTheme(preference) {
    if (!preference) { try{preference=localStorage.getItem('researchhelper.theme')}catch{} }
    if (!['light','dark','auto'].includes(preference))preference='light';
    const theme = preference==='auto'?(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light'):preference;
    document.documentElement.dataset.theme=theme;document.documentElement.dataset.themePref=preference;
    document.querySelectorAll('[data-theme-set]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.themeSet===preference)));
    send('theme',{theme,preference});
  }
  function payloadFor(form) {
    const value = id => form.querySelector('#'+id)?.value || '';
    const checked = id => form.querySelector('#'+id)?.checked === true;
    if (form.id==='model-form')return ['/api/settings/model',{endpoint:value('model-endpoint'),model:value('model-name'),api_key:value('model-key'),clear_key:checked('clear-model-key'),extraction_mode:value('model-extraction'),reasoning_effort:value('model-reasoning')||'default',planning_effort:value('model-planning')||'inherit'}];
    if (form.id==='data-form')return ['/api/settings/data',{ifind:{access_token:value('ifind-access-token'),refresh_token:value('ifind-refresh-token'),clear_access_token:checked('clear-ifind-access-token'),clear_refresh_token:checked('clear-ifind-refresh-token')},tinyshare:{token:value('tinyshare-token'),clear_token:checked('clear-tinyshare-token')}}];
    if (form.id==='execution-form')return ['/api/settings/execution',{enabled:checked('execution-enabled'),confirm:checked('execution-confirm')}];
    if (form.id==='sales-password-form') {
      if(value('sales-new-password')!==value('sales-password-confirm'))throw Error('两次密码不一致，请重新确认');
      return ['/api/auth/users/sales/password',{password:value('sales-new-password')}];
    }
    if (form.id==='claim-legacy-form')return ['/api/archive/claim-legacy',{confirm:checked('claim-confirm')}];
    throw Error('未知设置操作');
  }
  async function save(form) {
    if (!manager() || invalidated)return;
    if(saving){form.querySelector('.form-result').textContent='正在保存其他配置，请稍候';return}
    const feedback=form.querySelector('.form-result');
    let route,body;
    try{[route,body]=payloadFor(form)}catch(error){feedback.textContent=error.message;return}
    const controls=[...form.querySelectorAll('input,select,button')].map(control=>[control,control.disabled]);
    const secrets=[...form.querySelectorAll('input[type=password]')].map(input=>input.value).filter(Boolean);
    saving=true;form.setAttribute('aria-busy','true');controls.forEach(([control])=>control.disabled=true);
    feedback.textContent='正在保存并核对';let saved=false;
    try{
      await request(route,body);saved=true;adopt(await request('/api/bootstrap'));
      if(invalidated)return;
      if(['model-form','data-form','execution-form'].includes(form.id)) {
        const holder=document.createElement('div');holder.innerHTML=formHTML(form.id);const updated=holder.firstElementChild;
        form.replaceWith(updated);window.RHChoice.enhanceSelects(updated);
        updated.querySelector('.form-result').textContent='已保存并核对';updated.querySelector('button[type=submit]').focus({preventScroll:true});
      } else if(form.id==='claim-legacy-form'){
        const result=document.createElement('p');result.setAttribute('role','status');result.textContent='旧档案已归入admin';form.replaceWith(result);
      } else { form.reset();feedback.textContent='sales密码已重置，原登录已失效'; }
      $('#runtime-status').innerHTML=statusHTML();send('updated');
    }catch(error){
      let message=error.message;for(const secret of secrets)message=message.split(secret).join('已隐藏');
      if(!invalidated)feedback.textContent=(saved?'配置已保存，核对失败：':'保存结果未确认：')+message;
      if(saved)send('updated');
    }finally{
      form.querySelectorAll('input[type=password]').forEach(input=>input.value='');
      controls.forEach(([control,disabled])=>control.disabled=disabled);form.setAttribute('aria-busy','false');saving=false;
    }
  }
  document.addEventListener('submit',event=>{event.preventDefault();if(event.target.matches('.setup-form'))save(event.target)});
  document.addEventListener('change',event=>{
    if(event.target.id==='interface-scale')send('zoom',{scale:Number(event.target.value)});
    if(event.target.id==='model-preset' && ['deepseek-flash','deepseek-v4-pro'].includes(event.target.value)) {
      const form=event.target.closest('form');
      form.querySelector('#model-endpoint').value='https://api.deepseek.com';
      form.querySelector('#model-name').value=event.target.value;
      form.querySelector('.form-result').textContent='';
    }
  });
  document.addEventListener('input',event=>{
    if(!['model-endpoint','model-name'].includes(event.target.id))return;
    const form=event.target.closest('form'),preset=form?.querySelector('#model-preset');
    if(!preset)return;
    preset.value=modelPreset({endpoint:form.querySelector('#model-endpoint').value,model:form.querySelector('#model-name').value});
    preset.dispatchEvent(new Event('change',{bubbles:false}));
  });
  document.addEventListener('click',event=>{
    const link=event.target.closest('.settings-nav a');
    if(link){event.preventDefault();history.replaceState(null,'',link.hash);selectSection(link.hash.slice(1),true);return}
    const theme=event.target.closest('[data-theme-set]');
    if(theme){try{localStorage.setItem('researchhelper.theme',theme.dataset.themeSet)}catch{}syncTheme(theme.dataset.themeSet)}
    const action=event.target.closest('[data-workspace-action]');if(action)send('action',{action:action.dataset.workspaceAction});
  });
  $('.settings-close').addEventListener('click',()=>embedded?send('close'):location.assign('../index.html'));
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!event.defaultPrevented&&embedded){event.preventDefault();send('close')}});
  window.addEventListener('hashchange',()=>selectSection(location.hash.slice(1)));
  window.addEventListener('message',event=>{
    if(!embedded||event.source!==parent||event.origin!==(location.protocol==='file:'?'null':location.origin)||event.data?.instance!==instance||event.data?.type!=='researchhelper.settings-scale')return;
    const scale=event.data.scale;if(typeof scale!=='number'||![.8,.9,1,1.1,1.25,1.4].includes(scale))return;
    params.set('scale',String(scale));const select=$('#interface-scale');if(select){select.value=String(scale);select.dispatchEvent(new Event('input',{bubbles:true}))}
  });
  window.addEventListener('storage',event=>{
    if(event.key==='researchhelper.theme')syncTheme();
    if(event.key==='researchhelper-auth-event-v1'&&boot.authentication){try{if(JSON.parse(event.newValue)?.account!==boot.account)invalidate()}catch{}}
  });
  matchMedia('(prefers-color-scheme:dark)').addEventListener('change',()=>{if(document.documentElement.dataset.themePref==='auto')syncTheme('auto')});
  async function initialize() {
    try{adopt(apiMode?await request('/api/bootstrap'):window.RH_DEMO||{offline:true});render();$('#settings-status').textContent=''}
    catch(error){if(!invalidated){$('#settings-status').textContent=error.message;const retry=document.createElement('button');retry.type='button';retry.className='button-quiet';retry.textContent='重新连接';retry.onclick=()=>{retry.remove();initialize()};$('#settings-content').replaceChildren(retry)}}
  }
  // The parent only needs proof that close/recovery handlers are installed.
  // Bootstrap success, saved configuration and research permission stay separate.
  send('ready');
  initialize();
})();
