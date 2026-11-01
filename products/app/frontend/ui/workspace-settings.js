/* OH's embedded settings-dialog flow, adapted without its account or APIs. */
(() => {
  let active = null;
  window.RHWorkspaceSettings = {
    open(section, options = {}) {
      if (active?.open) return;
      const trigger = document.activeElement, dialog = document.createElement('dialog');
      dialog.className = 'workspace-settings-dialog';
      dialog.setAttribute('aria-label', '设置中心');
      let frame = null, instance = null, loadTimer = null, pendingAction = null;
      const recovery = document.createElement('section');
      recovery.className = 'workspace-settings-recovery';
      const title = document.createElement('h2');
      title.className = 'workspace-settings-load-title';title.setAttribute('role', 'status');
      const actions = document.createElement('div');actions.className = 'workspace-settings-recovery-actions';
      const retry = document.createElement('button'), close = document.createElement('button');
      for(const button of [retry,close])button.type = 'button';
      retry.textContent = '重新打开';retry.dataset.settingsRecovery = 'retry';
      close.textContent = '关闭设置';close.dataset.settingsRecovery = 'close';
      actions.append(retry,close);recovery.append(title,actions);dialog.append(recovery);
      const url = new URL('settings/index.html', location.href);
      url.searchParams.set('embedded', '1');
      const root=document.documentElement;
      const nativeHeight=Number.parseFloat(root.style.getPropertyValue('--native-titlebar-height'));
      const scale=Number(root.dataset.uiScale)||(root.dataset.nativeShell==='macos'&&nativeHeight?Math.round(36/nativeHeight*100)/100:Number(root.style.zoom)||1);
      url.searchParams.set('scale',String(scale));
      url.searchParams.set('owner', options.account || '');url.hash = section || 'preferences';
      const clearLoadTimer = () => {window.clearTimeout(loadTimer);loadTimer = null;};
      const isCurrent = current => active === dialog && dialog.open && current === frame;
      const failLoad = current => {
        if(!isCurrent(current))return;
        clearLoadTimer();title.textContent = '设置暂时无法打开';retry.hidden = false;
        recovery.hidden = false;frame.hidden = true;frame.inert = true;
      };
      const startLoad = () => {
        clearLoadTimer();
        const prior = frame;
        if(prior){prior.remove();prior.src = 'about:blank';}
        instance = crypto.randomUUID();url.searchParams.set('instance',instance);
        frame = document.createElement('iframe');frame.title = '设置中心';
        frame.hidden = true;frame.inert = true;
        const current = frame;
        current.addEventListener('error',()=>failLoad(current),{once:true});
        title.textContent = '正在打开设置';retry.hidden = true;recovery.hidden = false;
        frame.src = url.href;dialog.append(frame);
        // Like OH's module loader, a failed page must remain recoverable.
        // HTTP load alone is not readiness: a 404 can emit load successfully.
        loadTimer = window.setTimeout(()=>failLoad(current),12000);
      };
      retry.addEventListener('click',()=>{startLoad();close.focus({preventScroll:true});});
      close.addEventListener('click',()=>dialog.close());
      document.body.append(dialog);active = dialog;
      const syncScale=event=>frame?.contentWindow?.postMessage({type:'researchhelper.settings-scale',instance,scale:event.detail.scale},location.protocol==='file:'?'*':location.origin);
      document.addEventListener('researchhelper:uiscalechange',syncScale);
      const receive = event => {
        const expectedOrigin=location.protocol==='file:'?'null':location.origin;
        if (!isCurrent(frame) || event.origin !== expectedOrigin || event.source !== frame?.contentWindow || event.data?.instance !== instance) return;
        const message = event.data;
        if (message.type === 'researchhelper.settings-ready') {
          clearLoadTimer();recovery.hidden = true;frame.hidden = false;frame.inert = false;
          if(dialog.contains(document.activeElement))frame.focus({preventScroll:true});
        } else if (message.type === 'researchhelper.settings-close') dialog.close();
        else if (message.type === 'researchhelper.settings-updated') options.onUpdated?.();
        else if (message.type === 'researchhelper.settings-invalidated') { dialog.close();options.onInvalidated?.(); }
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
        } else if (message.type === 'researchhelper.settings-action' && ['cases','import','knowledge','logout','switch-account'].includes(message.action)) {
          pendingAction = message.action;dialog.close();
        }
      };
      window.addEventListener('message', receive);
      dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
      dialog.addEventListener('close', () => {
        clearLoadTimer();window.removeEventListener('message', receive);document.removeEventListener('researchhelper:uiscalechange',syncScale);dialog.remove();if(active === dialog)active = null;
        const focusable=element=>element?.isConnected&&element.getClientRects().length&&!element.closest('[inert]')&&!element.matches(':disabled')&&element.matches('a[href],button,input,select,textarea,[tabindex],[contenteditable]');
        const target = [trigger,document.querySelector('[data-account-menu-toggle]'),document.querySelector('#chat-input'),document.querySelector('#workspace')].find(focusable);
        target?.focus({preventScroll:true});
        if(!['logout','switch-account'].includes(pendingAction))options.onUpdated?.();
        if(pendingAction)options.onAction?.(pendingAction);
      }, {once:true});
      startLoad();dialog.showModal();
    },
    close() { active?.close(); },
  };
})();
