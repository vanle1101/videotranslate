/* Editor layout preferences are local UI state, never workflow configuration. */
document.addEventListener('DOMContentLoaded', () => {
  const workspace = document.getElementById('studio-workspace');
  if (!workspace) return;
  const storageKey = 'studio.column-widths.v1';
  const defaults = [.265, .425, .31];
  const minimums = [250, 360, 300];
  const columns = ['studio-controls', 'studio-preview', 'studio-sidebar'].map(id => document.getElementById(id));
  const handles = ['resize-left', 'resize-right'].map(id => document.getElementById(id));
  let proportions = [...defaults], drag = null, layoutEdited = false, nativeBridge = null, guideFrame = null;
  function validProportions(saved) {
    if (!Array.isArray(saved) || saved.length !== 3 || !saved.every(n => typeof n === 'number' && Number.isFinite(n) && n > 0)) return null;
    const sum = saved.reduce((a, b) => a + b, 0);
    if (!Number.isFinite(sum) || sum <= 0) return null;
    const normalized = saved.map(n => n / sum);
    return normalized.every(n => n > 0) ? normalized : null;
  }
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey));
    proportions = validProportions(saved) || proportions;
  } catch (_) { /* Browser storage may be disabled; resizing still works. */ }
  const desktop = () => window.matchMedia('(min-width: 1200px)').matches;
  function availableWidth() {
    const css = getComputedStyle(workspace);
    return Math.max(0, workspace.clientWidth - (parseFloat(css.paddingLeft) || 0) - (parseFloat(css.paddingRight) || 0) - 24);
  }
  function fittedWidths() {
    const total = availableWidth();
    const extra = Math.max(0, total - minimums.reduce((a, b) => a + b, 0));
    const requested = proportions.map((p, i) => Math.max(0, p * total - minimums[i]));
    const sum = requested.reduce((a, b) => a + b, 0);
    return minimums.map((min, i) => min + extra * (sum ? requested[i] / sum : defaults[i]));
  }
  function applyWidths(widths = fittedWidths()) {
    if (!desktop()) return;
    widths.forEach((width, i) => workspace.style.setProperty(`--column-${i}`, `${width}px`));
    handles.forEach((handle, i) => {
      const index = i === 0 ? 0 : 2;
      handle.setAttribute('aria-valuemin', String(minimums[index]));
      handle.setAttribute('aria-valuemax', String(Math.round(widths[i] + widths[i + 1] - minimums[1])));
      handle.setAttribute('aria-valuenow', String(Math.round(widths[index])));
      handle.setAttribute('aria-valuetext', `${Math.round(widths[index])} pixel`);
    });
  }
  function remember(widths) {
    const total = widths.reduce((a, b) => a + b, 0);
    if (!Number.isFinite(total) || total <= 0 || widths.some(width => !Number.isFinite(width) || width <= 0)) return;
    proportions = widths.map(width => width / total);
    layoutEdited = true;
    try { localStorage.setItem(storageKey, JSON.stringify(proportions)); } catch (_) { /* Optional preference. */ }
    nativeBridge?.setColumnWidths?.(JSON.stringify(proportions));
  }
  function moveBoundary(index, delta, widths, render = true) {
    const next = [...widths];
    const movement = Math.max(minimums[index] - widths[index], Math.min(delta, widths[index + 1] - minimums[index + 1]));
    next[index] += movement;
    next[index + 1] -= movement;
    if (render) applyWidths(next);
    return next;
  }
  function stopDrag(event, cancel = false) {
    if (!drag || (event?.pointerId !== undefined && event.pointerId !== drag.pointerId)) return;
    const finished = drag;
    drag = null;
    if (guideFrame !== null) window.cancelAnimationFrame(guideFrame);
    guideFrame = null;
    if (cancel) applyWidths(finished.widths);
    else { applyWidths(finished.latest); remember(finished.latest); }
    finished.handle.style.removeProperty('transform');
    workspace.classList.remove('is-resizing');
    if (finished.handle.hasPointerCapture(finished.pointerId)) finished.handle.releasePointerCapture(finished.pointerId);
  }
  function reset() {
    stopDrag(null, true);
    proportions = [...defaults];
    layoutEdited = true;
    try { localStorage.removeItem(storageKey); } catch (_) { /* Optional preference. */ }
    nativeBridge?.resetColumnWidths?.();
    applyWidths();
  }
  handles.forEach((handle, index) => {
    handle.addEventListener('pointerdown', event => {
      if (event.button !== 0 || !desktop() || drag) return;
      event.preventDefault();
      layoutEdited = true;
      handle.focus();
      drag = {handle, index, pointerId: event.pointerId, x: event.clientX, widths: columns.map(el => el.getBoundingClientRect().width)};
      drag.latest = [...drag.widths];
      handle.setPointerCapture(event.pointerId);
      workspace.classList.add('is-resizing');
    });
    handle.addEventListener('pointermove', event => {
      if (drag?.handle === handle && event.pointerId === drag.pointerId) {
        // Move only a composited guide while dragging. Reflowing hundreds of
        // transcript rows and video overlays on every mouse event stalls Qt.
        drag.latest = moveBoundary(index, event.clientX - drag.x, drag.widths, false);
        if (guideFrame === null) guideFrame = window.requestAnimationFrame(() => {
          guideFrame = null;
          if (drag?.handle === handle) {
            handle.style.setProperty('transform', `translateX(${drag.latest[index] - drag.widths[index]}px)`);
          }
        });
      }
    });
    handle.addEventListener('pointerup', event => stopDrag(event));
    handle.addEventListener('pointercancel', event => stopDrag(event, true));
    handle.addEventListener('lostpointercapture', event => stopDrag(event));
    handle.addEventListener('dblclick', reset);
    handle.addEventListener('keydown', event => {
      if (event.key === 'Escape' && drag) { event.preventDefault(); stopDrag(null, true); return; }
      if (event.key === 'Home') { event.preventDefault(); reset(); return; }
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key) || !desktop() || drag) return;
      event.preventDefault();
      const widths = columns.map(el => el.getBoundingClientRect().width);
      remember(moveBoundary(index, (event.key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 50 : 10), widths));
    });
  });
  window.addEventListener('blur', () => stopDrag(null));
  // A viewport change invalidates pointer coordinates and may switch to stacked
  // columns. Never store that stacked layout as the user's desktop preference.
  window.addEventListener('resize', () => { stopDrag(null, true); applyWidths(); });
  new ResizeObserver(() => { if (!drag) applyWidths(); }).observe(workspace);
  document.getElementById('btn-layout-reset').addEventListener('click', reset);
  applyWidths();
  window.restoreStudioLayout = bridge => {
    if (!bridge?.getColumnWidths || nativeBridge === bridge) return;
    nativeBridge = bridge;
    // Changes made before the asynchronous bridge arrives take precedence.
    if (layoutEdited) { bridge.setColumnWidths?.(JSON.stringify(proportions)); return; }
    bridge.getColumnWidths(raw => {
      if (layoutEdited || drag) return;
      try {
        const saved = validProportions(JSON.parse(raw));
        if (saved) {
          proportions = saved;
          try { localStorage.setItem(storageKey, JSON.stringify(saved)); } catch (_) { /* Optional preference. */ }
          applyWidths();
        }
      } catch (_) { /* An older or corrupted preference must not break layout. */ }
    });
  };
  window.restoreStudioLayout(window.desktopBridge);

  const tabs = ['translation', 'original', 'advanced'].map(name => document.getElementById(`editor-tab-${name}`));
  const transcript = document.getElementById('editor-transcript');
  const advanced = document.getElementById('editor-advanced');
  const list = document.getElementById('segments-list');
  function selectTab(index, focus = false) {
    tabs.forEach((tab, i) => { tab.setAttribute('aria-selected', String(i === index)); tab.tabIndex = i === index ? 0 : -1; });
    transcript.hidden = index === 2;
    advanced.hidden = index !== 2;
    transcript.setAttribute('aria-labelledby', tabs[index === 1 ? 1 : 0].id);
    transcript.dataset.mode = index === 1 ? 'original' : 'translation';
    const searchLabel = index === 1 ? 'Tìm trong lời gốc' : 'Tìm trong bản dịch tiếng Việt';
    search.placeholder = `${searchLabel}…`;
    search.setAttribute('aria-label', searchLabel);
    list.setAttribute('aria-label', index === 1 ? 'Lời gốc nhận diện từ video' : 'Bản dịch tiếng Việt có thể chỉnh sửa');
    filterTranscript();
    if (focus) tabs[index].focus();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => selectTab(index));
    tab.addEventListener('keydown', event => {
      const next = {ArrowRight: (index + 1) % 3, ArrowLeft: (index + 2) % 3, Home: 0, End: 2}[event.key];
      if (next !== undefined) { event.preventDefault(); selectTab(next, true); }
    });
  });
  const search = document.getElementById('transcript-search');
  const normalize = text => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/g, 'd').replace(/Đ/g, 'D').toLowerCase();
  function filterTranscript() {
    const query = normalize(search.value.trim());
    let visible = 0;
    list.querySelectorAll('.transcript-row').forEach(row => {
      const selector = transcript.dataset.mode === 'original' ? '.transcript-original' : '.transcript-translation';
      const text = [...row.querySelectorAll(selector)].map(el => el.textContent).join(' ');
      // Keep unsaved edits visible even if a worker changes text during a search.
      row.hidden = !!query && !normalize(text).includes(query) && row.dataset.editing !== 'true';
      if (!row.hidden) visible++;
    });
    document.getElementById('transcript-search-empty').hidden = !query || visible > 0;
  }
  search.addEventListener('input', filterTranscript);
  search.addEventListener('search', filterTranscript);
  search.addEventListener('keydown', event => {
    if (event.key === 'Escape' && search.value) { event.preventDefault(); search.value = ''; filterTranscript(); }
  });
  new MutationObserver(filterTranscript).observe(list, {childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['data-editing']});
  selectTab(0);
  const reveal = id => { const el = document.getElementById(id); el.scrollIntoView({block: 'nearest', behavior: 'auto'}); el.focus({preventScroll: true}); };
  document.getElementById('tool-captions').addEventListener('click', () => {
    selectTab(2, true);
    document.getElementById('caption-style-panel').open = true;
  });
  document.getElementById('tool-audio').addEventListener('click', () => reveal('vol-dub'));
  document.getElementById('tool-voice').addEventListener('click', () => reveal('voice-search'));
  const preview = document.getElementById('btn-source-preview');
  const start = document.getElementById('btn-start');
  const mirrorStart = () => { preview.disabled = start.disabled || start.classList.contains('hidden'); preview.title = start.title; };
  preview.addEventListener('click', () => { if (!preview.disabled) start.click(); });
  new MutationObserver(mirrorStart).observe(start, {attributes: true, attributeFilter: ['disabled', 'class', 'title']});
  mirrorStart();
});
