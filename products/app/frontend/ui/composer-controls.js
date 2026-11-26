/* OH input-height and attachment interaction patterns, adapted for RH materials.
 * No network, archive mutation, or second composer lives in this controller.
 */
(() => {
  'use strict';
  function create({input, form, panel, workArea, dropTarget, fileInput, queue, canQueue, onError}) {
    const shell = workArea.closest('[data-workspace-shell]');
    let clearanceFrame = 0, dragDepth = 0, lastWidth = -1, lastMode = null;
    const desktop = matchMedia('(min-width:701px)');
    const resetDrag = () => {dragDepth = 0;dropTarget.dataset.dragging = 'false'};
    const hasFiles = event => Array.from(event.dataTransfer?.types || []).includes('Files');
    const syncClearance = () => {
      cancelAnimationFrame(clearanceFrame);
      clearanceFrame = requestAnimationFrame(() => {
        clearanceFrame = 0;
        if (shell.dataset.mode !== 'chat' || !desktop.matches) {
          workArea.style.removeProperty('--chat-composer-clearance');
          return;
        }
        const bounds = dropTarget.getBoundingClientRect();
        if (bounds.height <= 0) return;
        // Browser CSS zoom changes rect coordinates; native pageZoom does not.
        const scale = dropTarget.offsetWidth ? bounds.width / dropTarget.offsetWidth : 1;
        const bottom = workArea.getBoundingClientRect().bottom;
        workArea.style.setProperty('--chat-composer-clearance', Math.max(0, Math.ceil((bottom - bounds.top) / scale + 12)) + 'px');
      });
    };
    const syncInputHeight = () => {
      if (!input.getClientRects().length || input.clientWidth <= 0) return;
      const style = getComputedStyle(input);
      const maximum = Math.min(120, Number.parseFloat(style.maxHeight) || 120);
      const minimum = Math.min(maximum, Math.max(30, Number.parseFloat(style.minHeight) || 30));
      input.style.height = 'auto';
      input.style.height = Math.min(maximum, Math.max(minimum, input.scrollHeight)) + 'px';
      input.dataset.overflow = String(input.scrollHeight > maximum);
      syncClearance();
    };
    const sync = () => {
      if (lastMode !== shell.dataset.mode) {lastMode = shell.dataset.mode;resetDrag()}
      syncInputHeight();
    };
    const add = async files => {try {await queue(Array.from(files))} catch (error) {onError(error.message)}};
    const paste = event => {
      const files = Array.from(event.clipboardData?.files || []);
      if (!files.length) return;
      event.preventDefault();add(files);
    };
    const dragEnter = event => {
      if (!hasFiles(event)) return;
      event.preventDefault();dragDepth += 1;dropTarget.dataset.dragging = 'true';
    };
    const dragOver = event => {
      if (!hasFiles(event)) return;
      event.preventDefault();event.dataTransfer.dropEffect = 'copy';
    };
    const dragLeave = event => {
      if (!hasFiles(event)) return;
      dragDepth = Math.max(0, dragDepth - 1);
      if (!dragDepth) resetDrag();
    };
    const drop = event => {
      if (!hasFiles(event)) return;
      event.preventDefault();resetDrag();add(event.dataTransfer.files);
    };
    // Reject document navigation on file drops outside the input surface.
    // Capture before queue rendering can remove the original event target.
    const guardNavigation = event => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      if (event.type === 'drop' && !dropTarget.contains(event.target)) {
        resetDrag();onError('请将材料拖入输入框');
      }
    };
    const escape = event => {if (event.key === 'Escape') resetDrag()};
    input.addEventListener('input', syncInputHeight);
    input.addEventListener('paste', paste);
    dropTarget.addEventListener('dragenter', dragEnter);
    dropTarget.addEventListener('dragover', dragOver);
    dropTarget.addEventListener('dragleave', dragLeave);
    dropTarget.addEventListener('drop', drop);
    document.addEventListener('dragover', guardNavigation, true);
    document.addEventListener('drop', guardNavigation, true);
    document.addEventListener('keydown', escape);
    window.addEventListener('blur', resetDrag);
    window.addEventListener('dragend', resetDrag);
    window.addEventListener('resize', sync);
    const resize = new ResizeObserver(() => {
      const width = form.clientWidth;
      if (width > 0 && width !== lastWidth) {lastWidth = width;syncInputHeight()}
      else syncClearance();
    });
    resize.observe(form);resize.observe(dropTarget);resize.observe(panel);
    sync();
    return {
      sync,
      choose() {try {canQueue();fileInput.click()} catch (error) {onError(error.message)}},
      destroy() {
        cancelAnimationFrame(clearanceFrame);resize.disconnect();resetDrag();
        input.removeEventListener('input', syncInputHeight);input.removeEventListener('paste', paste);
        dropTarget.removeEventListener('dragenter', dragEnter);dropTarget.removeEventListener('dragover', dragOver);
        dropTarget.removeEventListener('dragleave', dragLeave);dropTarget.removeEventListener('drop', drop);
        document.removeEventListener('dragover', guardNavigation, true);document.removeEventListener('drop', guardNavigation, true);
        document.removeEventListener('keydown', escape);window.removeEventListener('blur', resetDrag);
        window.removeEventListener('dragend', resetDrag);window.removeEventListener('resize', sync);
      },
    };
  }
  window.RHComposerControls = {create};
})();
