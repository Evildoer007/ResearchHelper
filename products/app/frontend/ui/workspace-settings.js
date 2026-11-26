/* OptionHelper settings dialog with ResearchHelper's local frame protocol. */
(() => {
  let active = null;
  window.RHWorkspaceSettings = {
    open(section, options = {}) {
      if (active) return;
      const trigger = document.activeElement, dialog = document.createElement('dialog');
      dialog.className = 'workspace-settings-dialog';
      dialog.setAttribute('aria-label', '设置中心');
      let frame = null, instance = null, loadTimer = null, pendingAction = null, closeInput = null;
      let loadState = 'loading', awaitingFocus = false;
      const clickInput = event => event.detail > 0 ? 'pointer' : 'keyboard';
      const dismiss = input => {
        if (input === 'pointer' || input === 'keyboard') closeInput = input;
        dialog.close();
      };
      const loading = document.createElement('div');
      loading.className = 'workspace-settings-dialog__loading';
      const status = document.createElement('p');
      status.setAttribute('role','status');
      const retry = document.createElement('button'), close = document.createElement('button');
      retry.type = close.type = 'button';
      retry.className = 'button-quiet';
      retry.textContent = '重试';
      close.className = 'workspace-settings-dialog__close';
      close.setAttribute('aria-label','关闭设置');
      close.title = '关闭设置';
      close.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
      loading.append(status,retry);
      dialog.append(loading,close);
      const url = new URL('settings/index.html', location.href);
      url.searchParams.set('embedded', '1');
      const root=document.documentElement;
      const readScale = () => {
        const nativeHeight=Number.parseFloat(root.style.getPropertyValue('--native-titlebar-height'));
        return Number(root.dataset.uiScale)||(root.dataset.nativeShell==='macos'&&nativeHeight
          ? Math.round(36/nativeHeight*100)/100 : Number(root.style.zoom)||1);
      };
      url.searchParams.set('owner', options.account || '');url.hash = section || 'preferences';
      const fileMode = location.protocol === 'file:';
      const clearLoadTimer = () => {window.clearTimeout(loadTimer);loadTimer = null;};
      const isCurrent = current => active === dialog && dialog.open && current === frame;
      const failLoad = current => {
        if(!isCurrent(current)||loadState!=='loading')return;
        clearLoadTimer();loadState = 'failed';
        status.textContent = '设置未能加载';retry.hidden = false;
      };
      const startLoad = () => {
        clearLoadTimer();
        awaitingFocus = false;
        const prior = frame;
        if(prior)prior.remove();
        instance = crypto.randomUUID();
        url.searchParams.set('instance',instance);
        url.searchParams.set('scale',String(readScale()));
        frame = document.createElement('iframe');frame.title = '设置中心';
        frame.hidden = true;frame.inert = true;
        const current = frame;
        current.addEventListener('error',()=>failLoad(current),{once:true});
        loadState = 'loading';status.textContent = '正在加载设置…';retry.hidden = true;
        frame.src = url.href;dialog.append(frame);
        // Like OH's module loader, a failed page must remain recoverable.
        // HTTP load alone is not readiness: a 404 can emit load successfully.
        loadTimer = window.setTimeout(()=>failLoad(current),12000);
      };
      retry.addEventListener('click',()=>{startLoad();close.focus({preventScroll:true});});
      close.addEventListener('click',event=>dismiss(clickInput(event)));
      document.body.append(dialog);active = dialog;
      const sendToFrame = (type, fields = {}) => frame?.contentWindow?.postMessage(
        {type:'researchhelper.settings-'+type,instance,...fields}, fileMode?'*':location.origin);
      const handOffFocus = () => {
        if(!awaitingFocus||!isCurrent(frame)||!document.hasFocus())return;
        const focused=document.activeElement;
        if(focused&&focused!==document.body&&focused!==document.documentElement&&!dialog.contains(focused))return;
        awaitingFocus = false;
        const innerDocument=fileMode?null:frame.contentDocument;
        const innerFocus=innerDocument?.activeElement;
        if(focused===frame&&innerFocus&&innerFocus!==innerDocument.body&&innerFocus!==innerDocument.documentElement)return;
        const innerClose=innerDocument?.querySelector('.settings-close');
        if(innerClose)innerClose.focus({preventScroll:true});
        else {frame.focus({preventScroll:true});sendToFrame('focus')}
      };
      const syncScale = event => {if(loadState==='ready')sendToFrame('scale',{scale:event.detail.scale})};
      document.addEventListener('researchhelper:uiscalechange',syncScale);
      window.addEventListener('focus',handOffFocus);
      dialog.addEventListener('focusin',handOffFocus);
      // Until settings is ready, keep Tab traversal within the available controls.
      dialog.addEventListener('keydown',event=>{
        if(event.key!=='Tab'||event.defaultPrevented||event.altKey||event.ctrlKey||event.metaKey||loadState==='ready')return;
        const first=retry.hidden?close:retry;
        if(document.activeElement!==(event.shiftKey?first:close))return;
        event.preventDefault();
        (event.shiftKey?close:first).focus({preventScroll:true});
      });
      const receive = event => {
        const expectedOrigin=fileMode?'null':location.origin;
        if (!isCurrent(frame) || event.origin !== expectedOrigin || event.source !== frame?.contentWindow || event.data?.instance !== instance) return;
        const message = event.data;
        if(message.type==='researchhelper.settings-invalidated'){dialog.close();options.onInvalidated?.();return}
        if(loadState==='failed')return;
        if (message.type === 'researchhelper.settings-ready') {
          if(loadState!=='loading')return;
          clearLoadTimer();loadState='ready';
          frame.hidden = false;frame.inert = false;loading.hidden = true;close.hidden = true;
          sendToFrame('scale',{scale:readScale()});
          awaitingFocus = true;
          handOffFocus();
        } else if (message.type === 'researchhelper.settings-failed') failLoad(frame);
        else if (message.type === 'researchhelper.settings-close') dismiss(message.input);
        else if (message.type === 'researchhelper.settings-updated') options.onUpdated?.();
        else if (message.type === 'researchhelper.settings-theme') {
          const {theme,preference} = message;
          if (!['light','dark'].includes(theme) || !['light','dark','auto'].includes(preference)) return;
          if(window.ResearchHelperTheme)window.ResearchHelperTheme.setThemePreference(preference);
          else {
            // Preserve the preference if the shared module is still loading.
            try{localStorage.setItem('researchhelper.theme',preference)}catch{}
            document.documentElement.dataset.theme = theme;document.documentElement.dataset.themePref = preference;
            document.dispatchEvent(new CustomEvent('researchhelper:themechange',{detail:{theme,preference}}));
          }
          try { window.webkit?.messageHandlers?.researchhelper?.postMessage({action:'theme',theme,preference}); } catch {}
        } else if (message.type === 'researchhelper.settings-zoom' && typeof message.scale==='number' && [.8,.9,1,1.1,1.25,1.4].includes(message.scale)) {
          if(window.webkit?.messageHandlers?.researchhelper)window.webkit.messageHandlers.researchhelper.postMessage({action:'zoom',scale:message.scale});
          else window.dispatchEvent(new CustomEvent('researchhelper:zoom',{detail:{scale:message.scale}}));
        } else if (message.type === 'researchhelper.settings-action' && ['cases','import','knowledge'].includes(message.action)) {
          pendingAction = message.action;dismiss(message.input);
        }
      };
      window.addEventListener('message', receive);
      dialog.addEventListener('click', event => { if (event.target === dialog) dismiss(clickInput(event)); });
      dialog.addEventListener('cancel', event => {event.preventDefault();dismiss('keyboard')});
      dialog.addEventListener('close', () => {
        clearLoadTimer();window.removeEventListener('message', receive);window.removeEventListener('focus',handOffFocus);
        document.removeEventListener('researchhelper:uiscalechange',syncScale);dialog.remove();if(active === dialog)active = null;
        const focusable=element=>element?.isConnected&&element.getClientRects().length&&!element.closest('[inert]')&&!element.matches(':disabled')&&element.matches('a[href],button,input,select,textarea,[tabindex],[contenteditable]');
        const target = [trigger,document.querySelector('[data-account-menu-toggle]'),document.querySelector('#chat-input'),document.querySelector('#workspace')].find(focusable);
        if (closeInput && target?.matches('[data-account-menu-toggle]')) window.RHWorkspaceControls?.setAccountInput(closeInput);
        target?.focus({preventScroll:true});
        options.onUpdated?.();
        if(pendingAction)options.onAction?.(pendingAction);
      }, {once:true});
      startLoad();dialog.showModal();close.focus({preventScroll:true});
    },
    close() { active?.close(); },
  };
})();
