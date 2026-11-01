/* OH conversation-scroll.js copied into RH; only offline loading and RH state adapter differ. */
(() => {
'use strict';
/* Give a submitted turn reading space without pulling users away from history. */
function createConversationScroll(stream, composer) {
  let anchor = null;
  let following = true;
  let frame = 0;
  let entering = false;
  let turnRevision = 0;
  let savedScroll = 0;
  const bottomGap = 40;
  const viewportHeight = () => {
    const bounds = stream.getBoundingClientRect();
    const composerTop = composer?.getBoundingClientRect().top;
    const bottom = composerTop > bounds.top ? Math.min(bounds.bottom, composerTop) : bounds.bottom;
    return Math.max(0, bottom - bounds.top);
  };
  const coordinate = node => node.getBoundingClientRect().top - stream.getBoundingClientRect().top + stream.scrollTop;
  const measure = () => {
    if (!anchor) return null;
    if (!anchor.isConnected || !stream.contains(anchor)) anchor = [...stream.querySelectorAll('.message--user')].at(-1);
    if (!anchor) { reset(); return null; }
    const height = viewportHeight();
    if (height < 80) return null;
    const visible = [...stream.children].filter(node => !node.hidden && node.getBoundingClientRect().height > 0);
    const last = visible.at(-1) || anchor;
    const top = coordinate(anchor);
    const tail = coordinate(last) + last.getBoundingClientRect().height;
    const inset = Math.min(72, Math.max(20, height * .14));
    // Padding shrinks as this reply grows; existing content never gains a fake height.
    const space = Math.max(0, stream.clientHeight - height)
      + Math.max(0, height - inset - (tail - top) - bottomGap);
    stream.style.setProperty('--conversation-turn-space', `${space}px`);
    return {top: Math.max(0, top - inset, tail - height + bottomGap), tail, height};
  };
  function refresh({previousScrollTop} = {}) {
    if (!anchor) return false;
    const layout = measure();
    if (layout && !entering) {
      if (following) stream.scrollTop = layout.top;
      else if (Number.isFinite(previousScrollTop)) stream.scrollTop = previousScrollTop;
      savedScroll = stream.scrollTop;
    }
    return true;
  }
  const schedule = () => {
    if (!frame) frame = requestAnimationFrame(() => { frame = 0; refresh(); });
  };
  const interrupt = () => { following = false; entering = false; };
  const wheel = event => { if (event.deltaY < 0) interrupt(); };
  const keydown = event => {
    if (['ArrowUp', 'PageUp', 'Home'].includes(event.key)) interrupt();
  };
  const scroll = () => {
    if (!entering && stream.scrollTop < savedScroll - 1) following = false;
    if (!entering && !following && stream.scrollTop > savedScroll) {
      const layout = measure();
      if (layout && layout.tail - stream.scrollTop <= layout.height - bottomGap + 24) following = true;
    }
    savedScroll = stream.scrollTop;
  };
  function reset() {
    turnRevision += 1;
    anchor = null; entering = false; following = true;
    stream.style.removeProperty('--conversation-turn-space');
    delete stream.dataset.turnAnchored;
  }
  const mutation = new MutationObserver(schedule);
  mutation.observe(stream, {childList: true, subtree: true, characterData: true});
  const resize = new ResizeObserver(schedule);
  resize.observe(stream);
  stream.addEventListener('wheel', wheel, {passive: true});
  stream.addEventListener('touchstart', interrupt, {passive: true});
  stream.addEventListener('pointerdown', interrupt, {passive: true});
  stream.addEventListener('keydown', keydown);
  stream.addEventListener('scroll', scroll, {passive: true});
  stream.addEventListener('load', schedule, true);
  const controller = {
    beginTurn(node) {
      const revision = ++turnRevision;
      anchor = node; following = true; entering = true;
      stream.dataset.turnAnchored = 'true';
      requestAnimationFrame(() => {
        if (revision !== turnRevision || !anchor || !entering) return;
        const layout = measure();
        if (layout) stream.scrollTo({top: layout.top, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
        // Streaming updates can resize the turn while its one entry scroll settles.
        window.setTimeout(() => {
          if (revision === turnRevision && entering) { entering = false; refresh(); }
        }, 450);
      });
    },
    // Explicit disclosure navigation must not be undone by streaming tail following.
    reveal(node) {
      interrupt();
      measure();
      const viewport = stream.getBoundingClientRect();
      const bounds = node.getBoundingClientRect();
      const top = viewport.top + 16;
      const bottom = viewport.top + viewportHeight() - 28;
      if (bounds.top >= top && bounds.bottom <= bottom) return;
      const target = Math.max(top, bottom - bounds.height);
      stream.scrollBy({top: bounds.top - target,
        behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    },
    refresh,
    reset,
    destroy() {
      reset(); cancelAnimationFrame(frame); mutation.disconnect(); resize.disconnect();
      stream.removeEventListener('wheel', wheel); stream.removeEventListener('touchstart', interrupt);
      stream.removeEventListener('pointerdown', interrupt);
      stream.removeEventListener('keydown', keydown); stream.removeEventListener('scroll', scroll);
      stream.removeEventListener('load', schedule, true);
      delete stream._conversationScroll;
    },
  };
  stream._conversationScroll = controller;
  return controller;
}

/* RH adapter: task/mode reading positions are presentation, never research state. */
function createConversationReader(stream, composer, {read, write}) {
  const turns = createConversationScroll(stream, composer);
  let key = null, pending = null, before = null, frame = 0, turnPending = false;
  const visible = () => stream.clientHeight > 0 && stream.getClientRects().length > 0
    && !stream.closest('[hidden], [inert]');
  const position = () => {
    const maximum = Math.max(0, stream.scrollHeight - stream.clientHeight);
    return {top: stream.scrollTop, ratio: maximum > 0 ? stream.scrollTop / maximum : 0,
      atBottom: maximum > 0 && maximum - stream.scrollTop <= 2,
      width: stream.clientWidth, height: stream.clientHeight};
  };
  const valid = saved => saved && Number.isFinite(saved.top) && saved.top >= 0
    && Number.isFinite(saved.ratio) && saved.ratio >= 0 && saved.ratio <= 1
    && typeof saved.atBottom === 'boolean';
  const capture = () => {
    if (!key || pending || frame || !visible()) return;
    const state = position();
    write(key, state);
    return state;
  };
  const updated = ({changed = false} = {}) => {
    if (!key) return;
    if (changed && !before && !pending) before = read(key);
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      frame = 0;
      if (!key || !visible()) return;
      if (turnPending) {
        turnPending = false; pending = null; before = null;
        const anchor = [...stream.querySelectorAll('.message--user')].at(-1);
        if (anchor) turns.beginTurn(anchor);
        return;
      }
      if (pending) {
        const maximum = Math.max(0, stream.scrollHeight - stream.clientHeight);
        stream.scrollTop = pending.atBottom ? maximum
          : pending.width && Math.abs(pending.width - stream.clientWidth) > 1
            ? pending.ratio * maximum : pending.top;
        pending = null;
      } else if (before) {
        if (!turns.refresh({previousScrollTop: before.top}))
          stream.scrollTop = before.atBottom ? stream.scrollHeight : before.top;
      } else turns.refresh();
      before = null;
      capture();
    });
  };
  const enter = next => {
    if (next === key) {
      if (!frame && !pending) before = capture() || before;
      return;
    }
    capture(); cancelAnimationFrame(frame); frame = 0;
    turns.reset(); before = null; turnPending = false; key = next;
    const saved = next ? read(next) : null;
    pending = next ? valid(saved) ? {...saved} : {top:0, ratio:0, atBottom:false} : null;
  };
  const scroll = () => capture();
  // A poll can recreate an already-open progress element. Only an actual
  // summary interaction is disclosure navigation; rendering must not seize it.
  const disclosureIntents = new WeakSet();
  const intendDisclosure = event => {
    if (event.type === 'keydown' && !['Enter',' '].includes(event.key)) return;
    const summary = event.target.closest('summary');
    if (summary?.parentElement.matches('details')) disclosureIntents.add(summary.parentElement);
  };
  const disclose = event => {
    const element = event.target;
    if (!element.matches('details') || !disclosureIntents.has(element)) return;
    disclosureIntents.delete(element);
    if (element.open) turns.reveal(element);
  };
  stream.addEventListener('scroll', scroll, {passive:true});
  stream.addEventListener('click', intendDisclosure, true);
  stream.addEventListener('keydown', intendDisclosure, true);
  stream.addEventListener('toggle', disclose, true);
  const resize = new ResizeObserver(() => updated());
  resize.observe(stream);
  return {
    enter, capture, updated,
    beginTurn() { turnPending = true; updated(); },
    navigate(target) {
      if (!key || !visible() || !target || !stream.contains(target)) return;
      capture(); cancelAnimationFrame(frame); frame = 0;
      pending = null; before = null; turnPending = false;
      // Explicit history navigation owns the reading position. Clear pending
      // OH turn entry frames so a late reply cannot pull this jump to the tail.
      turns.reset();
      const bounds = stream.getBoundingClientRect();
      const scale = stream.offsetWidth ? bounds.width / stream.offsetWidth : 1;
      const top = stream.scrollTop + (target.getBoundingClientRect().top - bounds.top) / (scale || 1) - stream.clientTop - 16;
      stream.scrollTo({top: Math.max(0, top),
        behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
      capture();
    },
    destroy() {
      cancelAnimationFrame(frame); frame = 0; key = null; pending = null;
      stream.removeEventListener('scroll', scroll); stream.removeEventListener('toggle', disclose, true);
      stream.removeEventListener('click', intendDisclosure, true);
      stream.removeEventListener('keydown', intendDisclosure, true);
      resize.disconnect(); turns.destroy();
    },
  };
}
window.RHConversationScroll = {createConversationScroll, createConversationReader};
})();

