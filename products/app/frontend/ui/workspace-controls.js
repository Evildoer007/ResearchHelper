/* OptionHelper account-menu and rail-splitter patterns, adapted for RH only.
 * Presentation state lives here; authentication and task mutations stay in workspace.js.
 */
(() => {
  'use strict';
  const shell = document.querySelector('[data-workspace-shell]');
  if (!shell) return;
  const rail = shell.querySelector('.workspace-rail');
  const root = shell.querySelector('[data-account-menu-root]');
  const toggle = root.querySelector('[data-account-menu-toggle]');
  const menu = root.querySelector('.rail-account-menu');
  const splitter = shell.querySelector('[data-workspace-splitter="rail"]');
  const contextSplitter = shell.querySelector('.workspace-splitter--context');
  contextSplitter.dataset.workspaceSplitter = 'context';
  contextSplitter.setAttribute('role','separator');contextSplitter.setAttribute('aria-orientation','vertical');
  contextSplitter.setAttribute('aria-label','调整交付栏宽度');
  const historyToggle = shell.querySelector('[data-action="tasks"]');
  const mobile = matchMedia('(max-width:700px)');
  const compactReport = matchMedia('(max-width:1100px)');
  const minimum = 197, maximum = 440, fallback = 260;
  const namespace = 'researchhelper.workspace.';
  const stored = (key) => { try { return localStorage.getItem(namespace + key); } catch { return null; } };
  const save = (key, value) => { try { localStorage.setItem(namespace + key, String(value)); } catch { /* optional layout preference */ } };
  const storedWidth = Number(stored('rail'));
  let preferredWidth = Number.isFinite(storedWidth) && storedWidth >= minimum ? Math.min(maximum, storedWidth) : fallback;
  let stopDrag = null;
  let stopReportDrag = null;
  const storedReport = Number(stored('report'));
  let preferredReport = Number.isFinite(storedReport) && storedReport >= 280 ? Math.min(480, storedReport) : 332;
  let lastNativeCollapsed = null;
  const items = () => Array.from(menu.querySelectorAll('[role="menuitem"]')).filter(item => !item.hidden && !item.disabled);
  const focusItem = (position) => {
    const available = items();
    if (!available.length) return;
    const index = available.indexOf(document.activeElement);
    (position === 'first' ? available[0] : position === 'last' ? available.at(-1) : available[(index + position + available.length) % available.length]).focus();
  };
  const closeAccountMenu = (restoreFocus = false) => {
    menu.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    rail.dataset.accountMenuOpen = 'false';
    if (restoreFocus) toggle.focus();
  };
  const openAccountMenu = (position = 'first') => {
    menu.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    rail.dataset.accountMenuOpen = 'true';
    focusItem(position);
  };
  toggle.addEventListener('click', () => menu.hidden ? openAccountMenu() : closeAccountMenu());
  toggle.addEventListener('keydown', event => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    openAccountMenu(['ArrowUp', 'End'].includes(event.key) ? 'last' : 'first');
  });
  menu.addEventListener('click', event => {
    if (event.target.closest('[role="menuitem"]:not([disabled])')) closeAccountMenu(true);
  }, {capture: true});
  document.addEventListener('pointerdown', event => {
    if (!root.contains(event.target)) closeAccountMenu();
  });
  document.addEventListener('keydown', event => {
    if (menu.hidden) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeAccountMenu(true);
    } else if (menu.contains(event.target) && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      focusItem(event.key === 'Home' ? 'first' : event.key === 'End' ? 'last' : event.key === 'ArrowDown' ? 1 : -1);
    } else if (event.key === 'Tab') closeAccountMenu();
  });
  const maximumFor = () => {
    const contextWidth = shell.dataset.reportOpen === 'true' && innerWidth > 1100
      ? Number.parseFloat(getComputedStyle(shell).getPropertyValue('--report-width')) || 332 : 0;
    return Math.max(minimum, Math.min(maximum, Math.floor(shell.getBoundingClientRect().width / coordinateScale() - 560 - 8 - contextWidth)));
  };
  const readWidth = () => Number.parseFloat(getComputedStyle(shell).getPropertyValue('--rail-width')) || fallback;
  const applyWidth = (value, persist = false) => {
    const limit = maximumFor();
    const width = Math.max(minimum, Math.min(limit, value));
    shell.style.setProperty('--rail-width', width + 'px');
    splitter.setAttribute('aria-valuenow', String(Math.round(width)));
    splitter.setAttribute('aria-valuemin', String(minimum));
    splitter.setAttribute('aria-valuemax', String(limit));
    if (persist) { preferredWidth = width; save('rail', Math.round(width)); }
  };
  const readReportWidth = () => Number.parseFloat(getComputedStyle(shell).getPropertyValue('--report-width')) || 332;
  const reportMaximum = () => {
    const railWidth = shell.dataset.railCollapsed === 'true' ? 0 : readWidth();
    return Math.max(280, Math.min(480, Math.floor(shell.getBoundingClientRect().width / coordinateScale() - 560 - 16 - railWidth)));
  };
  const applyReportWidth = (value, persist = false) => {
    const limit = reportMaximum(), width = Math.max(280, Math.min(limit, value));
    shell.style.setProperty('--report-width',width + 'px');
    contextSplitter.setAttribute('aria-valuenow',String(Math.round(width)));
    contextSplitter.setAttribute('aria-valuemin','280');contextSplitter.setAttribute('aria-valuemax',String(limit));
    if (persist) {preferredReport = width;save('report',Math.round(width));}
  };
  const syncRail = () => {
    const collapsed = !mobile.matches && shell.dataset.railCollapsed === 'true';
    const visible = mobile.matches ? rail.classList.contains('visible') : !collapsed;
    const label = visible ? '收起任务历史' : '打开任务历史';
    historyToggle.setAttribute('aria-expanded', String(visible));
    historyToggle.setAttribute('aria-label', label);
    historyToggle.title = label;
    splitter.tabIndex = mobile.matches || collapsed ? -1 : 0;
    splitter.setAttribute('aria-hidden', String(mobile.matches || collapsed));
    if (!visible) { closeAccountMenu(); stopDrag?.(); }
    const reportResizable = shell.dataset.reportOpen === 'true' && !compactReport.matches;
    contextSplitter.tabIndex = reportResizable ? 0 : -1;
    contextSplitter.setAttribute('aria-hidden',String(!reportResizable));
    if (!reportResizable) stopReportDrag?.();
    applyReportWidth(preferredReport);
    applyWidth(preferredWidth);
    if (location.protocol === 'http:' && location.pathname === '/' && !mobile.matches && lastNativeCollapsed !== collapsed) {
      const bridge = window.webkit?.messageHandlers?.researchhelper;
      if (bridge) {
        try { bridge.postMessage({action:'rail-state', collapsed}); lastNativeCollapsed = collapsed; } catch { /* optional native shell */ }
      }
    }
  };
  const setRailCollapsed = collapsed => {
    shell.dataset.railCollapsed = String(Boolean(collapsed));
    save('railCollapsed', Boolean(collapsed));
    syncRail();
  };
  splitter.addEventListener('pointerdown', event => {
    if (event.button !== 0 || mobile.matches || shell.dataset.railCollapsed === 'true') return;
    event.preventDefault();
    stopDrag?.();
    const startX = event.clientX, startWidth = readWidth(), pointer = event.pointerId, scale = coordinateScale();
    shell.dataset.resizing = 'true';
    splitter.focus();
    splitter.setPointerCapture?.(pointer);
    const move = next => { if (next.pointerId === pointer) applyWidth(startWidth + (next.clientX - startX) / scale); };
    const stop = () => {
      shell.dataset.resizing = 'false';
      preferredWidth = readWidth(); save('rail', Math.round(preferredWidth));
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', finish);
      window.removeEventListener('pointercancel', finish);
      window.removeEventListener('blur', stop);
      if (splitter.hasPointerCapture?.(pointer)) splitter.releasePointerCapture(pointer);
      stopDrag = null;
    };
    const finish = next => { if (next.pointerId === pointer) stop(); };
    stopDrag = stop;
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', finish);
    window.addEventListener('pointercancel', finish);
    window.addEventListener('blur', stop);
  });
  splitter.addEventListener('keydown', event => {
    if (mobile.matches || shell.dataset.railCollapsed === 'true') return;
    const step = event.shiftKey ? 40 : 16;
    const next = {ArrowLeft: readWidth() - step, ArrowRight: readWidth() + step, Home: minimum, End: maximumFor()};
    if (!(event.key in next)) return;
    event.preventDefault(); applyWidth(next[event.key], true);
  });
  // OH report-library separator, independent preference and no research write.
  contextSplitter.addEventListener('pointerdown', event => {
    if (event.button !== 0 || compactReport.matches || shell.dataset.reportOpen !== 'true') return;
    event.preventDefault();stopReportDrag?.();stopDrag?.();
    const startX = event.clientX, startWidth = readReportWidth(), pointer = event.pointerId, scale = coordinateScale();
    shell.dataset.resizing = 'true';contextSplitter.focus();contextSplitter.setPointerCapture?.(pointer);
    const move = next => {if (next.pointerId === pointer) applyReportWidth(startWidth + (startX - next.clientX) / scale);};
    const stop = () => {
      shell.dataset.resizing = 'false';preferredReport = readReportWidth();save('report',Math.round(preferredReport));
      window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',finish);
      window.removeEventListener('pointercancel',finish);window.removeEventListener('blur',stop);
      contextSplitter.removeEventListener('lostpointercapture',stop);
      if (contextSplitter.hasPointerCapture?.(pointer)) contextSplitter.releasePointerCapture(pointer);
      stopReportDrag = null;
    };
    const finish = next => {if (next.pointerId === pointer) stop();};
    stopReportDrag = stop;
    window.addEventListener('pointermove',move);window.addEventListener('pointerup',finish);window.addEventListener('pointercancel',finish);
    window.addEventListener('blur',stop);contextSplitter.addEventListener('lostpointercapture',stop);
  });
  contextSplitter.addEventListener('keydown', event => {
    if (compactReport.matches || shell.dataset.reportOpen !== 'true') return;
    const step = event.shiftKey ? 40 : 16;
    const next = {ArrowLeft:readReportWidth() + step,ArrowRight:readReportWidth() - step,Home:280,End:reportMaximum()};
    if (!(event.key in next)) return;
    event.preventDefault();applyReportWidth(next[event.key],true);
  });
  // The same focus boundary as OH's mobile task sheet; the work area is inert.
  rail.addEventListener('keydown', event => {
    if (event.key !== 'Tab' || !mobile.matches || !rail.classList.contains('visible')) return;
    const available = Array.from(rail.querySelectorAll('button:not([disabled]), input:not([disabled]), [tabindex="0"]'))
      .filter(item => item.tabIndex >= 0 && !item.closest('[hidden],[inert]') && item.getClientRects().length);
    const first = available[0], last = available.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  });
  new MutationObserver(syncRail).observe(shell, {attributes:true, attributeFilter:['data-rail-collapsed', 'data-report-open']});
  new MutationObserver(syncRail).observe(rail, {attributes:true, attributeFilter:['class']});
  window.addEventListener('resize', syncRail);
  mobile.addEventListener('change', () => { stopDrag?.(); closeAccountMenu(); syncRail(); });
  compactReport.addEventListener('change',syncRail);
  // Port OH's measured module indicator, retaining RH's five research views.
  const viewTabs = shell.querySelector('.views');
  const viewIndicator = viewTabs.querySelector('.desk-module-tabs__indicator');
  let indicatorFrame = 0;
  const coordinateScale = () => Number.parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
  const syncViewIndicator = (animate = true) => {
    cancelAnimationFrame(indicatorFrame);
    indicatorFrame = requestAnimationFrame(() => {
      const active = viewTabs.querySelector('[aria-selected="true"]');
      const bounds = viewTabs.getBoundingClientRect(), selected = active?.getBoundingClientRect();
      if (!selected?.width || !bounds.width) return;
      const scale = coordinateScale();
      if (!animate) viewIndicator.style.transition = 'none';
      viewIndicator.style.width = selected.width / scale + 'px';
      viewIndicator.style.transform = 'translate3d(' + ((selected.left - bounds.left) / scale + viewTabs.scrollLeft) + 'px,0,0)';
      viewIndicator.dataset.ready = 'true';
      if (!animate) { void viewIndicator.offsetWidth; viewIndicator.style.removeProperty('transition'); }
    });
  };
  new ResizeObserver(() => syncViewIndicator(false)).observe(viewTabs);
  viewTabs.addEventListener('scroll', () => syncViewIndicator(false), {passive:true});
  window.addEventListener('resize', () => syncViewIndicator(false), {passive:true});

  // The same upper-left resize handle as OH; the shared composer is never
  // replaced. CSS zoom needs client-to-layout conversion in the offline demo.
  const assistantPanel = shell.querySelector('#assistant-panel');
  const assistantResize = assistantPanel.querySelector('[data-assistant-resize]');
  let assistantDrag = null;
  const assistantAvailable = () => !mobile.matches && shell.dataset.mode === 'desk'
    && shell.querySelector('.assistant-region').classList.contains('is-open') && !assistantPanel.inert;
  const resizeAssistant = (width, height) => {
    const scale = coordinateScale(), bounds = assistantPanel.getBoundingClientRect();
    const maxWidth = Math.max(0, Math.min(innerWidth / scale - 24, bounds.right / scale - 12));
    const maxHeight = Math.max(0, Math.min(innerHeight / scale - 24, bounds.bottom / scale - 12));
    assistantPanel.style.width = Math.min(maxWidth, Math.max(Math.min(360, maxWidth), width)) + 'px';
    assistantPanel.style.height = Math.min(maxHeight, Math.max(Math.min(360, maxHeight), height)) + 'px';
  };
  const finishAssistantResize = () => {
    if (!assistantDrag) return;
    const pointer = assistantDrag.pointer;
    assistantDrag = null;
    assistantPanel.classList.remove('is-resizing');
    if (assistantResize.hasPointerCapture?.(pointer)) assistantResize.releasePointerCapture(pointer);
  };
  const syncAssistant = () => {
    const available = assistantAvailable();
    assistantResize.hidden = !available;assistantResize.tabIndex = available ? 0 : -1;
    if (!available) { finishAssistantResize(); return; }
    if (assistantPanel.style.width) resizeAssistant(parseFloat(assistantPanel.style.width),parseFloat(assistantPanel.style.height));
  };
  assistantResize.addEventListener('pointerdown', event => {
    if (event.button !== 0 || !assistantAvailable()) return;
    finishAssistantResize();
    const scale = coordinateScale(), bounds = assistantPanel.getBoundingClientRect();
    assistantDrag = {x:event.clientX,y:event.clientY,width:bounds.width / scale,height:bounds.height / scale,pointer:event.pointerId,scale};
    assistantResize.focus();assistantResize.setPointerCapture?.(event.pointerId);
    assistantPanel.classList.add('is-resizing');event.preventDefault();
  });
  assistantResize.addEventListener('pointermove', event => {
    if (!assistantDrag || event.pointerId !== assistantDrag.pointer) return;
    resizeAssistant(assistantDrag.width + (assistantDrag.x - event.clientX) / assistantDrag.scale,
      assistantDrag.height + (assistantDrag.y - event.clientY) / assistantDrag.scale);
  });
  for (const type of ['pointerup','pointercancel','lostpointercapture']) assistantResize.addEventListener(type, finishAssistantResize);
  window.addEventListener('blur', finishAssistantResize);
  assistantResize.addEventListener('keydown', event => {
    const delta = {ArrowLeft:[24,0],ArrowRight:[-24,0],ArrowUp:[0,24],ArrowDown:[0,-24]}[event.key];
    if (!delta || !assistantAvailable()) return;
    event.preventDefault();
    const bounds = assistantPanel.getBoundingClientRect(), scale = coordinateScale();
    resizeAssistant(bounds.width / scale + delta[0],bounds.height / scale + delta[1]);
  });
  window.addEventListener('resize', () => { finishAssistantResize();syncAssistant(); }, {passive:true});
  mobile.addEventListener('change', syncAssistant);
  shell.dataset.railCollapsed = String(stored('railCollapsed') === 'true');
  syncRail();
  requestAnimationFrame(() => requestAnimationFrame(() => { shell.dataset.layoutReady = 'true'; }));
  let workMenu=null;
  const menuItems=menu=>[...menu.querySelectorAll('[role="menuitem"]:not(:disabled)')];
  function closeWorkMenu(restore=false){
    if(!workMenu)return;
    const menu=workMenu;workMenu=null;
    menu.querySelector('[role="menu"]').hidden=true;
    const button=menu.querySelector('[data-work-menu-toggle]');button.setAttribute('aria-expanded','false');
    if(restore&&button.isConnected)button.focus({preventScroll:true});
  }
  function openWorkMenu(menu,focus=false){
    closeWorkMenu();workMenu=menu;
    menu.querySelector('[role="menu"]').hidden=false;
    menu.querySelector('[data-work-menu-toggle]').setAttribute('aria-expanded','true');
    if(focus)menuItems(menu)[0]?.focus({preventScroll:true});
  }
  document.addEventListener('click',event=>{
    const trigger=event.target.closest('[data-work-menu-toggle]');
    if(trigger){const menu=trigger.closest('[data-work-menu]');if(workMenu===menu)closeWorkMenu();else openWorkMenu(menu);return;}
    if(event.target.closest('[data-work-menu] [role="menuitem"]')){closeWorkMenu(true);return;}
    if(workMenu&&!workMenu.contains(event.target))closeWorkMenu();
  });
  document.addEventListener('keydown',event=>{
    const trigger=event.target.closest('[data-work-menu-toggle]');
    if(trigger&&['ArrowDown','ArrowUp'].includes(event.key)){
      event.preventDefault();openWorkMenu(trigger.closest('[data-work-menu]'),true);
      if(event.key==='ArrowUp')menuItems(workMenu).at(-1)?.focus();return;
    }
    if(!workMenu)return;
    if(event.key==='Escape'){event.preventDefault();closeWorkMenu(true);return;}
    if(event.key==='Tab'){closeWorkMenu(true);return;}
    if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
    event.preventDefault();const items=menuItems(workMenu),index=items.indexOf(document.activeElement);
    const next=event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;
    items[next]?.focus();
  });
  window.RHWorkspaceControls = {
    setRailCollapsed, closeAccountMenu, syncRail, syncAssistant, syncViewIndicator,
    setAccount(account, authenticated) {
      root.querySelectorAll('[data-account-label], [data-account-menu-label]').forEach(label => { label.textContent = account || '离线研究'; });
      root.querySelectorAll('[data-auth-required]').forEach(item => { item.hidden = !authenticated; });
      toggle.setAttribute('aria-label', '账户与设置，' + (account || '离线研究'));
    },
  };
})();
