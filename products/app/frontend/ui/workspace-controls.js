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
  const compactReport = matchMedia('(max-width:1220px)');
  const minimum = 197, maximum = 440;
  const fallback = Number.parseFloat(getComputedStyle(shell).getPropertyValue('--rail-width'));
  const namespace = 'researchhelper.workspace.';
  const stored = (key) => { try { return localStorage.getItem(namespace + key); } catch { return null; } };
  const save = (key, value) => { try { localStorage.setItem(namespace + key, String(value)); } catch { /* optional layout preference */ } };
  const storedWidth = Number(stored('rail'));
  let preferredRail = Number.isFinite(storedWidth) && storedWidth > 0
    ? Math.max(minimum, Math.min(maximum, storedWidth)) : fallback;
  let stopDrag = null;
  let stopReportDrag = null;
  const storedReport = Number(stored('report'));
  let preferredReport = Number.isFinite(storedReport) && storedReport >= 280 ? Math.min(480, storedReport) : 332;
  let lastNativeCollapsed = null;
  const accountNavigationKeys = new Set(['Tab','Enter',' ','Escape','ArrowDown','ArrowUp','Home','End']);
  const items = () => Array.from(menu.querySelectorAll('[role="menuitem"]')).filter(item => !item.hidden && !item.disabled);
  const setAccountInput = input => root.toggleAttribute('data-account-pointer', input === 'pointer');
  const focusAccountControl = element => element?.focus({preventScroll:true});
  const focusItem = (position) => {
    const available = items();
    if (!available.length) return;
    const index = available.indexOf(document.activeElement);
    const next = position === 'first' ? available[0] : position === 'last' ? available.at(-1) : available[(index + position + available.length) % available.length];
    focusAccountControl(next);
  };
  const closeAccountMenu = (restoreFocus = false) => {
    menu.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    rail.dataset.accountMenuOpen = 'false';
    if (restoreFocus) focusAccountControl(toggle);
  };
  const openAccountMenu = position => {
    menu.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    rail.dataset.accountMenuOpen = 'true';
    if (position) focusItem(position);
  };
  toggle.addEventListener('click', event => {
    const pointer = event.detail > 0;
    setAccountInput(pointer ? 'pointer' : 'keyboard');
    if (pointer) focusAccountControl(toggle);
    if (menu.hidden) openAccountMenu(pointer ? null : 'first');
    else closeAccountMenu();
  });
  toggle.addEventListener('keydown', event => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    setAccountInput('keyboard');
    openAccountMenu(['ArrowUp', 'End'].includes(event.key) ? 'last' : 'first');
  });
  menu.addEventListener('click', event => {
    const item = event.target.closest('[role="menuitem"]:not(:disabled):not([hidden])');
    if (!item || !menu.contains(item)) return;
    setAccountInput(event.detail > 0 ? 'pointer' : 'keyboard');
    closeAccountMenu(true);
  }, {capture: true});
  document.addEventListener('pointerdown', event => {
    setAccountInput('pointer');
    if (!menu.hidden && !toggle.contains(event.target) && !menu.contains(event.target)) closeAccountMenu();
  });
  document.addEventListener('keydown', event => {
    if (accountNavigationKeys.has(event.key)) setAccountInput('keyboard');
    if (menu.hidden || event.defaultPrevented) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeAccountMenu(true);
    } else if (menu.contains(event.target) && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      focusItem(event.key === 'Home' ? 'first' : event.key === 'End' ? 'last' : event.key === 'ArrowDown' ? 1 : -1);
    } else if (event.key === 'Tab') {
      // Let the browser continue traversal from the persistent trigger.
      closeAccountMenu(menu.contains(event.target));
    }
  });
  const maximumFor = () => {
    const reportOpen = shell.dataset.reportOpen === 'true';
    const contextWidth = reportOpen && !compactReport.matches
      ? Number.parseFloat(getComputedStyle(shell).getPropertyValue('--report-width')) || 332 : 0;
    const splitterWidth = reportOpen ? 16 : 8;
    return Math.max(minimum, Math.min(maximum, Math.floor(shell.getBoundingClientRect().width / coordinateScale() - 560 - splitterWidth - contextWidth)));
  };
  const readWidth = () => Number.parseFloat(getComputedStyle(shell).getPropertyValue('--rail-width')) || fallback;
  const applyWidth = value => {
    const limit = maximumFor();
    const width = Math.max(minimum, Math.min(limit, value));
    shell.style.setProperty('--rail-width', width + 'px');
    splitter.setAttribute('aria-valuenow', String(Math.round(width)));
    splitter.setAttribute('aria-valuemin', String(minimum));
    splitter.setAttribute('aria-valuemax', String(limit));
    return width;
  };
  const saveRailWidth = width => {
    preferredRail = Math.round(width);
    save('rail', preferredRail);
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
    // The viewport may temporarily narrow the rail. Keep the user's chosen width.
    if (!stopDrag) applyWidth(preferredRail);
    applyReportWidth(preferredReport);
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
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', finish);
      window.removeEventListener('pointercancel', finish);
      window.removeEventListener('blur', stop);
      if (splitter.hasPointerCapture?.(pointer)) splitter.releasePointerCapture(pointer);
      stopDrag = null;
      applyWidth(preferredRail);
    };
    const finish = next => {
      if (next.pointerId !== pointer) return;
      const width = readWidth();
      if (next.type === 'pointerup' && width !== startWidth) saveRailWidth(width);
      stop();
    };
    stopDrag = stop;
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', finish);
    window.addEventListener('pointercancel', finish);
    window.addEventListener('blur', stop);
  });
  splitter.addEventListener('keydown', event => {
    if (mobile.matches || shell.dataset.railCollapsed === 'true') return;
    const width = readWidth(), step = event.shiftKey ? 40 : 16;
    const next = {ArrowLeft: width - step, ArrowRight: width + step, Home: minimum, End: maximumFor()};
    if (!(event.key in next)) return;
    event.preventDefault();
    const resized = applyWidth(next[event.key]);
    if (resized !== width) saveRailWidth(resized);
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
  const activeWorkTrigger = () => shell.querySelector('[data-work-menu-toggle][aria-expanded="true"]');
  const workMenuItems = menu => [...menu.querySelectorAll('[role="menuitem"]:not(:disabled):not([hidden])')];
  function closeWorkMenu(restoreFocus = false) {
    const trigger = activeWorkTrigger();
    if (!trigger) return;
    trigger.closest('[data-work-menu]').querySelector('[role="menu"]').hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    if (restoreFocus) trigger.focus({preventScroll: true});
  }
  function openWorkMenu(trigger, position) {
    closeWorkMenu();
    const menu = trigger.closest('[data-work-menu]').querySelector('[role="menu"]');
    menu.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    const items = workMenuItems(menu);
    const destination = position === 'first' ? items[0] : position === 'last' ? items.at(-1) : trigger;
    (destination || trigger).focus({preventScroll: true});
  }
  document.addEventListener('click', event => {
    const trigger = event.target.closest('[data-work-menu-toggle]');
    const active = activeWorkTrigger();
    if (trigger) {
      if (trigger === active) closeWorkMenu(true);
      else openWorkMenu(trigger, event.detail > 0 ? null : 'first');
    } else if (active && active.closest('[data-work-menu]').contains(event.target.closest('[role="menuitem"]'))) {
      closeWorkMenu(true);
    }
  }, {capture: true});
  for (const eventType of ['pointerdown', 'focusin']) {
    document.addEventListener(eventType, event => {
      const active = activeWorkTrigger();
      if (active && !active.closest('[data-work-menu]').contains(event.target)) closeWorkMenu();
    });
  }
  const menuNavigation = {ArrowDown: 1, ArrowUp: -1, Home: 0, End: -1};
  document.addEventListener('keydown', event => {
    if (event.defaultPrevented) return;
    const trigger = event.target.closest('[data-work-menu-toggle]');
    const navigationKey = Object.hasOwn(menuNavigation, event.key);
    if (trigger && navigationKey) {
      event.preventDefault();
      openWorkMenu(trigger, ['ArrowUp', 'End'].includes(event.key) ? 'last' : 'first');
      return;
    }
    const menu = activeWorkTrigger()?.closest('[data-work-menu]');
    if (!menu?.contains(event.target)) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeWorkMenu(true);
    } else if (event.key === 'Tab') {
      closeWorkMenu(true);
    } else if (navigationKey) {
      event.preventDefault();
      const items = workMenuItems(menu);
      const index = items.indexOf(document.activeElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
        : (index + menuNavigation[event.key] + items.length) % items.length;
      items[next]?.focus({preventScroll: true});
    }
  });
  window.RHWorkspaceControls = {
    compactReport, setRailCollapsed, closeAccountMenu, closeWorkMenu, setAccountInput, syncRail, syncAssistant, syncViewIndicator,
    setAccount(account, authenticated) {
      root.querySelectorAll('[data-account-label], [data-account-menu-label]').forEach(label => { label.textContent = account || '离线研究'; });
      root.querySelectorAll('[data-auth-required]').forEach(item => { item.hidden = !authenticated; });
      toggle.setAttribute('aria-label', '账户与设置，' + (account || '离线研究'));
    },
  };
})();
