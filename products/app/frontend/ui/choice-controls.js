/* Extracted from OH shared/app.js choiceLabel..enhanceSelects. No account or service imports. */
(()=>{
const choiceViewports = new WeakMap();

function choiceViewport(doc) {
  if (choiceViewports.has(doc)) return choiceViewports.get(doc);
  const view = doc.defaultView;
  const opened = new Set();
  let frame = 0;
  const schedule = () => {
    if (frame || !opened.size) return;
    frame = view.requestAnimationFrame(() => {
      frame = 0;
      for (const choice of Array.from(opened)) {
        const trigger = choice.querySelector('.choice-trigger');
        if (!choice.isConnected || !isChoiceOpen(choice) || trigger?.disabled || !trigger?.getClientRects().length) {
          setChoiceOpen(choice, false);
          continue;
        }
        positionChoiceMenu(choice);
        choice.querySelector('.choice-menu').style.transformOrigin = choice.dataset.placement === 'top' ? 'bottom center' : 'top center';
      }
    });
  };
  const observer = typeof view.ResizeObserver === 'function' ? new view.ResizeObserver(schedule) : null;
  const viewport = {
    track(choice, open) {
      const trigger = choice.querySelector('.choice-trigger');
      if (open) { opened.add(choice); if (trigger) observer?.observe(trigger); }
      else { opened.delete(choice); if (trigger) observer?.unobserve(trigger); }
      if (!opened.size && frame) { view.cancelAnimationFrame(frame); frame = 0; }
    }
  };
  // As with OH's task-menu controller, listen once per document. Only an open
  // choice is observed, and scrolling its own option list never resets it.
  doc.addEventListener('scroll', (event) => {
    if (!event.target.closest?.('.choice-menu')) schedule();
  }, {capture: true, passive: true});
  view.addEventListener('resize', schedule, {passive: true});
  view.visualViewport?.addEventListener('resize', schedule, {passive: true});
  view.visualViewport?.addEventListener('scroll', schedule, {passive: true});
  choiceViewports.set(doc, viewport);
  return viewport;
}

function choiceLabel(select) {
  const explicit = select.getAttribute("aria-label");
  if (explicit) return explicit;
  if (select.id) {
    const explicitLabel = Array.from(select.ownerDocument.querySelectorAll("label[for]")).find((label) => label.htmlFor === select.id);
    if (explicitLabel?.textContent.trim()) return explicitLabel.textContent.trim();
  }
  const label = select.closest("label");
  if (!label) return "选择选项";
  const clone = label.cloneNode(true);
  clone.querySelectorAll("select, button, [role=listbox]").forEach((node) => node.remove());
  return clone.textContent.trim() || "选择选项";
}

function optionFor(select, value) {
  return Array.from(select.options).find((option) => option.value === value) || select.selectedOptions[0] || select.options[0];
}

function isChoiceOpen(choice) {
  return choice.dataset.open === "true";
}

function positionChoiceMenu(choice) {
  const trigger = choice.querySelector(".choice-trigger");
  const menu = choice.querySelector(".choice-menu");
  if (!trigger || !menu || menu.hidden) return;
  const rect = trigger.getBoundingClientRect();
  const view = choice.ownerDocument.defaultView;
  const viewportHeight = view?.innerHeight || document.documentElement.clientHeight;
  let top = 0;
  let bottom = viewportHeight;
  for (let parent = choice.parentElement; parent; parent = parent.parentElement) {
    if (!/(auto|scroll|hidden|clip)/.test(view.getComputedStyle(parent).overflowY)) continue;
    const bounds = parent.getBoundingClientRect();
    top = Math.max(top, bounds.top);
    bottom = Math.min(bottom, bounds.bottom);
  }
  if (menu.hasAttribute("popover")) {
    top = 0;
    bottom = viewportHeight;
  }
  const desiredHeight = Math.min(menu.scrollHeight || 160, 280);
  const roomBelow = Math.max(0, bottom - rect.bottom - 6);
  const roomAbove = Math.max(0, rect.top - top - 6);
  choice.dataset.placement = roomBelow < desiredHeight && roomAbove > roomBelow ? "top" : "bottom";
  menu.style.maxHeight = `${Math.min(280, viewportHeight * .42, choice.dataset.placement === "top" ? roomAbove : roomBelow)}px`;
  if (menu.hasAttribute("popover")) {
    const preferredWidth = choice.dataset.choiceLayout === "research-depth" ? 136
      : choice.closest(".model-picker") ? Math.max(rect.width, 280) : Math.max(rect.width, 270);
    const width = Math.min(preferredWidth, view.innerWidth - 24);
    menu.style.boxSizing = "border-box";
    menu.style.position = "fixed";
    menu.style.margin = "0";
    menu.style.width = `${width}px`;
    menu.style.left = `${Math.max(12, Math.min(rect.left, view.innerWidth - width - 12))}px`;
    menu.style.right = "auto";
    menu.style.top = choice.dataset.placement === "top" ? "auto" : `${rect.bottom + 7}px`;
    menu.style.bottom = choice.dataset.placement === "top" ? `${viewportHeight - rect.top + 7}px` : "auto";
  }
}

function setChoiceOpen(choice, open, { focus = false } = {}) {
  const trigger = choice.querySelector(".choice-trigger");
  const menu = choice.querySelector(".choice-menu");
  if (!trigger || !menu || (trigger.disabled && open)) return;
  if (open) choice.ownerDocument.querySelectorAll('[data-choice-root][data-open="true"]').forEach((other) => {
    if (other !== choice) setChoiceOpen(other, false);
  });
  choice.dataset.open = String(open);
  trigger.setAttribute("aria-expanded", String(open));
  choiceViewport(choice.ownerDocument).track(choice, open);
  if (open) {
    menu.hidden = false;
    menu.inert = false;
    if (menu.hasAttribute("popover") && !menu.matches(":popover-open")) menu.showPopover();
    positionChoiceMenu(choice);
    menu.style.transformOrigin = choice.dataset.placement === "top" ? "bottom center" : "top center";
    // Dropdown entry motion is shared with module pages in choice-controls.css.
  } else {
    menu.inert = true;
    menu.hidden = true;
    if (menu.hasAttribute("popover") && menu.matches(":popover-open")) menu.hidePopover();
  }
  if (open && focus) {
    const selected = menu.querySelector('[role="option"][aria-selected="true"]:not([disabled])') || menu.querySelector('[role="option"]:not([disabled])');
    selected?.focus({ preventScroll: true });
  } else if (!open && focus) {
    trigger.focus({ preventScroll: true });
  }
}

function renderChoiceLabel(node, option) {
  node.replaceChildren();
  const label = node.ownerDocument.createElement("span");
  label.className = "choice-label";
  label.textContent = option?.textContent || "未选择";
  node.append(label);
  if (option?.dataset.english) {
    const english = node.ownerDocument.createElement("span");
    english.className = "choice-english";
    english.lang = "en";
    english.textContent = option.dataset.english;
    node.append(english);
  }
}

function syncChoice(select) {
  const choice = select.closest("[data-choice-root]");
  if (!choice) return;
  const trigger = choice.querySelector(".choice-trigger");
  const value = choice.querySelector(".choice-trigger__value");
  const menu = choice.querySelector(".choice-menu");
  if (!trigger || !value || !menu) return;
  const selected = optionFor(select, select.value);
  const researchDepth = select.dataset.choiceLayout === "research-depth";
  if (researchDepth) { choice.dataset.choiceLayout = "research-depth"; menu.setAttribute("aria-orientation", "horizontal"); }
  renderChoiceLabel(value, selected);
  trigger.disabled = select.disabled || !select.options.length;
  trigger.setAttribute("aria-disabled", String(trigger.disabled));
  if (select.getAttribute("aria-invalid") === "true") trigger.setAttribute("aria-invalid", "true");
  else trigger.removeAttribute("aria-invalid");
  const describedBy = select.getAttribute("aria-describedby");
  if (describedBy) trigger.setAttribute("aria-describedby", describedBy);
  else trigger.removeAttribute("aria-describedby");
  const doc = select.ownerDocument;
  const focusedValue = menu.contains(doc.activeElement) ? doc.activeElement.dataset.value : null;
  const renderOption = (option) => {
    const item = doc.createElement("button");
    item.type = "button";
    item.className = researchDepth ? "choice-option choice-option--depth" : "choice-option";
    item.setAttribute("role", "option");
    item.dataset.value = option.value;
    renderChoiceLabel(item, option);
    item.disabled = select.disabled || option.disabled || Boolean(option.closest("optgroup")?.disabled);
    item.setAttribute("aria-selected", String(option.selected));
    item.tabIndex = -1;
    item.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (item.disabled || select.disabled) return;
      const changed = select.value !== option.value;
      setChoiceOpen(choice, false);
      select.value = option.value;
      if (changed) select.dispatchEvent(new Event("change", { bubbles: true }));
      if (trigger.isConnected && !select.disabled) trigger.focus({ preventScroll: true });
    });
    return item;
  };
  menu.replaceChildren(...Array.from(select.children).map((child) => {
    if (child.tagName !== "OPTGROUP") return renderOption(child);
    const group = doc.createElement("span");
    group.className = "choice-group";
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", child.label);
    const heading = doc.createElement("span");
    heading.className = "choice-group__label";
    heading.setAttribute("aria-hidden", "true");
    heading.textContent = child.label;
    group.append(heading, ...Array.from(child.children).map(renderOption));
    return group;
  }));
  if (choice.closest(".composer .model-picker")) {
    const heading = doc.createElement("span");
    heading.className = "choice-menu__heading";
    heading.setAttribute("aria-hidden", "true");
    heading.textContent = "当前运行环境";
    menu.prepend(heading);
  }
  if (trigger.disabled) setChoiceOpen(choice, false);
  else if (focusedValue !== null && isChoiceOpen(choice)) {
    const options = Array.from(menu.querySelectorAll('[role="option"]:not([disabled])'));
    (options.find((item) => item.dataset.value === focusedValue) || options.find((item) => item.dataset.value === select.value) || trigger).focus({ preventScroll: true });
    positionChoiceMenu(choice);
  }
}

function moveChoice(select, direction) {
  const enabled = Array.from(select.options).filter((option) => !option.disabled && !option.closest("optgroup")?.disabled);
  if (!enabled.length) return;
  const index = Math.max(0, enabled.findIndex((option) => option.value === select.value));
  const next = enabled[Math.max(0, Math.min(enabled.length - 1, index + direction))];
  if (!next || next.value === select.value) return;
  select.value = next.value;
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

/**
 * Renders an accessible, product-owned choice menu while retaining the source
 * select for form serialization and programmatic integrations. The original
 * select is intentionally removed from the tab order; the trigger supports
 * the standard select keyboard sequence (Space/Enter, arrows, Home/End, Esc).
 */
function enhanceSelects(root = document) {
  const doc = root.ownerDocument || root;
  const selects = root.querySelectorAll?.("select[data-choice]") || [];
  selects.forEach((select) => {
    if (select.closest("[data-choice-root]")) {
      syncChoice(select);
      return;
    }
    const menuId = `choice-${globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2)}`;
    const choice = doc.createElement("span");
    choice.className = "choice optionhelper-choice";
    choice.dataset.choiceRoot = "true";
    const trigger = doc.createElement("button");
    trigger.type = "button";
    trigger.id = `${menuId}-trigger`;
    trigger.className = "choice-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", menuId);
    trigger.setAttribute("aria-label", choiceLabel(select));
    trigger.innerHTML = '<span class="choice-trigger__value"></span><span class="choice-trigger__chevron" aria-hidden="true"></span>';
    const menu = doc.createElement("span");
    menu.id = menuId;
    menu.className = "choice-menu";
    if (select.closest(".composer") && typeof menu.showPopover === "function") menu.setAttribute("popover", "manual");
    menu.setAttribute("role", "listbox");
    menu.setAttribute("aria-label", choiceLabel(select));
    menu.hidden = true;
    select.classList.add("choice-native");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");
    select.after(choice);
    choice.append(select, trigger, menu);
    const explicitLabels = Array.from(doc.querySelectorAll("label[for]")).filter((label) => label.htmlFor === select.id);
    explicitLabels.forEach((label) => { label.htmlFor = trigger.id; });
    const wrappingLabel = choice.closest("label");
    if (wrappingLabel && !wrappingLabel.htmlFor) {
      wrappingLabel.addEventListener("click", (event) => {
        if (event.target.closest('[data-choice-root], input, select, textarea, button, a[href], [contenteditable="true"]')) return;
        event.preventDefault();
        trigger.focus();
      });
    }
    const close = (focusTrigger = false) => setChoiceOpen(choice, false, { focus: focusTrigger });
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      setChoiceOpen(choice, !isChoiceOpen(choice), { focus: isChoiceOpen(choice) === false });
    });
    trigger.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !isChoiceOpen(choice)) return;
      if (["ArrowDown", "ArrowUp", "Home", "End", " ", "Enter", "Escape"].includes(event.key)) event.stopPropagation();
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (isChoiceOpen(choice)) moveChoice(select, event.key === "ArrowDown" ? 1 : -1);
        else setChoiceOpen(choice, true, { focus: true });
      } else if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        const options = Array.from(select.options).filter((option) => !option.disabled && !option.closest("optgroup")?.disabled);
        const option = options[event.key === "Home" ? 0 : options.length - 1];
        if (option && option.value !== select.value) { select.value = option.value; select.dispatchEvent(new Event("change", { bubbles: true })); }
      } else if (event.key === "Escape") {
        event.preventDefault();
        close();
      } else if (event.key === " " || event.key === "Enter") {
        event.preventDefault();
        if (event.repeat) return;
        setChoiceOpen(choice, !isChoiceOpen(choice), { focus: !isChoiceOpen(choice) });
      }
    });
    menu.addEventListener("keydown", (event) => {
      const key = select.dataset.choiceLayout === "research-depth"
        ? ({ ArrowLeft: "ArrowUp", ArrowRight: "ArrowDown" }[event.key] || event.key) : event.key;
      if (["ArrowDown", "ArrowUp", "Home", "End", " ", "Enter", "Escape"].includes(key)) event.stopPropagation();
      const options = Array.from(menu.querySelectorAll('[role="option"]:not([disabled])'));
      const at = options.indexOf(doc.activeElement);
      if (key === "ArrowDown" || key === "ArrowUp") {
        event.preventDefault();
        (options[Math.max(0, Math.min(options.length - 1, at + (key === "ArrowDown" ? 1 : -1)))] || options[0])?.focus();
      } else if (key === "Home" || key === "End") {
        event.preventDefault();
        options[key === "Home" ? 0 : options.length - 1]?.focus();
      } else if (key === "Escape") {
        event.preventDefault(); close(true);
      } else if (key === "Tab") {
        close(true);
      } else if (key === "Enter" || key === " ") {
        event.preventDefault();
        if (event.repeat) return;
        doc.activeElement?.click();
      }
    });
    select.addEventListener("input", () => syncChoice(select));
    select.addEventListener("change", () => syncChoice(select));
    new MutationObserver(() => syncChoice(select)).observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-describedby", "aria-invalid", "disabled", "selected", "label"] });
    syncChoice(select);
  });
  if (doc.documentElement.dataset.choiceOutsideReady !== "true") {
    doc.documentElement.dataset.choiceOutsideReady = "true";
    doc.addEventListener("pointerdown", (event) => {
      doc.querySelectorAll('[data-choice-root][data-open="true"]').forEach((choice) => {
        if (!choice.contains(event.target)) setChoiceOpen(choice, false);
      });
    });
    doc.addEventListener("focusin", (event) => {
      doc.querySelectorAll('[data-choice-root][data-open="true"]').forEach((choice) => {
        if (!choice.contains(event.target)) setChoiceOpen(choice, false);
      });
    });
  }
}


window.RHChoice={enhanceSelects};
})();
