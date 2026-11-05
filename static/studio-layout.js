(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else {
    root.StudioLayout = api;
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => api.initialize(document, root));
    else api.initialize(document, root);
  }
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';
  const STORAGE_KEY = 'studio-layout-v1';
  const HANDLE_SIZE = 10;
  const CONFIG = {
    columns: { axis: 'x', defaults: [.25, .43, .32], minimums: [250, 360, 280] },
    left: { axis: 'y', defaults: [.42, .58], minimums: [120, 180] },
    middle: { axis: 'y', defaults: [.60, .25, .15], minimums: [220, 120, 100] },
    right: { axis: 'y', defaults: [.55, .45], minimums: [180, 180] },
  };

  function normalize(value, fallback) {
    if (!Array.isArray(value) || value.length !== fallback.length ||
        value.some(number => typeof number !== 'number' || !Number.isFinite(number) || number <= 0)) return fallback.slice();
    const sum = value.reduce((a, b) => a + b, 0);
    if (!Number.isFinite(sum) || sum <= 0) return fallback.slice();
    return value.map(number => number / sum);
  }

  function readPreferences(raw) {
    let saved = {};
    try { saved = JSON.parse(raw) || {}; } catch (_) { /* A damaged preference cannot prevent Studio opening. */ }
    return Object.fromEntries(Object.entries(CONFIG).map(([name, config]) => [name, normalize(saved[name], config.defaults)]));
  }

  function effectiveMinimums(total, minimums) {
    const sum = minimums.reduce((a, b) => a + b, 0);
    const scale = Math.min(1, Math.max(0, total) / sum);
    return minimums.map(value => value * scale);
  }

  // Redistribute only the remaining space after panes hitting their minimum are fixed.
  function fitSizes(total, ratios, minimums) {
    total = Math.max(0, total);
    const floors = effectiveMinimums(total, minimums);
    const sizes = Array(ratios.length).fill(0);
    let pending = ratios.map((_, index) => index);
    let remaining = total;
    while (pending.length) {
      const weight = pending.reduce((sum, index) => sum + ratios[index], 0);
      const constrained = pending.filter(index => remaining * ratios[index] / weight < floors[index]);
      if (!constrained.length) {
        pending.forEach(index => { sizes[index] = remaining * ratios[index] / weight; });
        break;
      }
      constrained.forEach(index => { sizes[index] = floors[index]; remaining -= floors[index]; });
      pending = pending.filter(index => !constrained.includes(index));
    }
    return sizes;
  }

  function resizePair(sizes, index, delta, minimums) {
    const result = sizes.slice();
    const total = sizes.reduce((a, b) => a + b, 0);
    const floors = effectiveMinimums(total, minimums);
    const pair = sizes[index] + sizes[index + 1];
    const next = Math.max(floors[index], Math.min(pair - floors[index + 1], sizes[index] + delta));
    result[index] = next;
    result[index + 1] = pair - next;
    return result;
  }

  function initialize(doc, win) {
    const workspace = doc.getElementById('studio-layout');
    if (!workspace || workspace.dataset.layoutInitialized === 'true') return;
    workspace.dataset.layoutInitialized = 'true';
    let stored = null;
    try { stored = win.localStorage.getItem(STORAGE_KEY); } catch (_) { /* Private / restricted storage. */ }
    let preferences = readPreferences(stored);
    const desktop = win.matchMedia('(min-width: 1200px)');
    const groups = Array.from(doc.querySelectorAll('[data-layout-group]')).map(element => {
      const name = element.dataset.layoutGroup;
      const config = CONFIG[name];
      if (!config) return null;
      return { element, name, config, handles: Array.from(element.children).filter(child => child.dataset.layoutAxis === config.axis) };
    }).filter(Boolean);
    let drag = null;
    let frame = null;
    let revision = 0;
    let saveTimer = null;
    let saveQueue = Promise.resolve();
    const layoutStatus = doc.getElementById('studio-layout-status');

    function announce(message) { if (layoutStatus) layoutStatus.textContent = message; }
    function saveToServer(reset = false) {
      if (!win.fetch) return;
      if (saveTimer !== null) win.clearTimeout(saveTimer);
      const body = JSON.stringify({ version: 1, groups: reset ? {} : preferences });
      saveTimer = win.setTimeout(() => {
        saveTimer = null;
        // Keep writes in order even if the backend is temporarily slow.
        saveQueue = saveQueue.then(async () => {
          try {
            const response = await win.fetch('/api/studio/layout', {
              method: 'PUT', headers: { 'Content-Type': 'application/json' }, body,
            });
            if (!response.ok) throw new Error('Layout save failed');
            announce(reset ? 'Đã khôi phục bố cục mặc định.' : 'Đã lưu bố cục.');
          } catch (_) { announce('Đã áp dụng bố cục trong cửa sổ này; chưa lưu được vào ứng dụng.'); }
        });
      }, 350);
    }

    function available(group) {
      const horizontal = group.config.axis === 'x';
      const style = win.getComputedStyle(group.element);
      const padding = horizontal ? ['paddingLeft', 'paddingRight'] : ['paddingTop', 'paddingBottom'];
      const size = horizontal ? group.element.clientWidth : group.element.clientHeight;
      return Math.max(0, size - padding.reduce((sum, key) => sum + (parseFloat(style[key]) || 0), 0) - HANDLE_SIZE * (group.config.defaults.length - 1));
    }

    function visible(group) {
      return group.element.getClientRects().length > 0 && available(group) > 0;
    }

    function renderGroup(group) {
      const property = group.config.axis === 'x' ? 'gridTemplateColumns' : 'gridTemplateRows';
      group.handles.forEach(handle => {
        handle.tabIndex = desktop.matches ? 0 : -1;
        handle.setAttribute('aria-disabled', desktop.matches ? 'false' : 'true');
      });
      if (!desktop.matches) { group.element.style[property] = ''; return; }
      if (!visible(group)) return;
      const total = available(group);
      const sizes = fitSizes(total, preferences[group.name], group.config.minimums);
      const tracks = sizes.map(size => `${Math.max(size, .001).toFixed(4)}fr`).join(` ${HANDLE_SIZE}px `);
      if (group.element.style[property] !== tracks) group.element.style[property] = tracks;
      const floors = effectiveMinimums(total, group.config.minimums);
      group.handles.forEach(handle => {
        const index = Number(handle.dataset.layoutIndex);
        handle.setAttribute('aria-orientation', group.config.axis === 'x' ? 'vertical' : 'horizontal');
        handle.setAttribute('aria-valuenow', String(Math.round(sizes[index])));
        handle.setAttribute('aria-valuemin', String(Math.round(floors[index])));
        handle.setAttribute('aria-valuemax', String(Math.round(sizes[index] + sizes[index + 1] - floors[index + 1])));
        handle.setAttribute('aria-valuetext', `${Math.round(sizes[index])} pixel`);
      });
    }

    function render() { groups.forEach(renderGroup); }
    function scheduleRender() {
      if (frame !== null) return;
      frame = win.requestAnimationFrame(() => { frame = null; render(); });
    }
    function persist() {
      try { win.localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences)); } catch (_) { /* Resizing still works without persistence. */ }
      saveToServer();
    }
    function applySizes(group, sizes) {
      revision++;
      preferences[group.name] = normalize(sizes, group.config.defaults);
      renderGroup(group);
    }
    function finishDrag(cancelled) {
      if (!drag) return;
      const previous = drag;
      drag = null;
      if (cancelled) { preferences[previous.group.name] = previous.original; renderGroup(previous.group); }
      else persist();
      previous.handle.classList.remove('is-dragging');
      delete previous.handle.dataset.dragging;
      doc.documentElement.classList.remove('studio-layout-dragging');
      doc.body?.classList.remove('studio-resizing');
      if (previous.handle.hasPointerCapture?.(previous.pointerId)) previous.handle.releasePointerCapture(previous.pointerId);
    }

    groups.forEach(group => group.handles.forEach(handle => {
      const index = Number(handle.dataset.layoutIndex);
      if (!Number.isInteger(index) || index < 0 || index >= group.config.defaults.length - 1) return;
      handle.addEventListener('pointerdown', event => {
        if (!desktop.matches || !visible(group) || event.button !== 0 || drag) return;
        event.preventDefault();
        handle.focus({ preventScroll: true });
        drag = { group, handle, index, pointerId: event.pointerId,
          coordinate: group.config.axis === 'x' ? event.clientX : event.clientY,
          sizes: fitSizes(available(group), preferences[group.name], group.config.minimums),
          original: preferences[group.name].slice() };
        handle.setPointerCapture(event.pointerId);
        handle.classList.add('is-dragging');
        handle.dataset.dragging = 'true';
        doc.documentElement.classList.add('studio-layout-dragging');
        doc.body?.classList.add('studio-resizing');
      });
      handle.addEventListener('pointermove', event => {
        if (!drag || drag.handle !== handle || drag.pointerId !== event.pointerId) return;
        const coordinate = group.config.axis === 'x' ? event.clientX : event.clientY;
        applySizes(group, resizePair(drag.sizes, index, coordinate - drag.coordinate, group.config.minimums));
      });
      handle.addEventListener('pointerup', event => { if (drag?.handle === handle && drag.pointerId === event.pointerId) finishDrag(false); });
      handle.addEventListener('pointercancel', event => { if (drag?.handle === handle && drag.pointerId === event.pointerId) finishDrag(true); });
      handle.addEventListener('lostpointercapture', () => { if (drag?.handle === handle) finishDrag(true); });
      handle.addEventListener('keydown', event => {
        if (!desktop.matches || !visible(group)) return;
        const negative = group.config.axis === 'x' ? 'ArrowLeft' : 'ArrowUp';
        const positive = group.config.axis === 'x' ? 'ArrowRight' : 'ArrowDown';
        if (![negative, positive, 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const sizes = fitSizes(available(group), preferences[group.name], group.config.minimums);
        const delta = event.key === 'Home' ? -Infinity : event.key === 'End' ? Infinity : (event.shiftKey ? 50 : 10) * (event.key === negative ? -1 : 1);
        applySizes(group, resizePair(sizes, index, delta, group.config.minimums));
        persist();
      });
    }));
    doc.getElementById('btn-reset-layout')?.addEventListener('click', () => {
      finishDrag(true);
      revision++;
      preferences = readPreferences(null);
      try { win.localStorage.removeItem(STORAGE_KEY); } catch (_) { /* Storage is optional. */ }
      render();
      announce('Đã khôi phục bố cục mặc định.');
      saveToServer(true);
    });
    const onViewportChange = () => { finishDrag(true); scheduleRender(); };
    win.addEventListener('resize', onViewportChange);
    if (desktop.addEventListener) desktop.addEventListener('change', onViewportChange);
    else desktop.addListener(onViewportChange);
    if (win.ResizeObserver) {
      const observer = new win.ResizeObserver(scheduleRender);
      groups.forEach(group => observer.observe(group.element));
    }
    render();
    if (win.fetch) {
      // The launcher uses a new port on restart, so server preferences survive
      // changes of browser origin. Do not override a drag begun while loading.
      win.fetch('/api/studio/layout').then(response => response.ok ? response.json() : Promise.reject(new Error('Layout load failed')))
        .then(saved => {
          if (revision || drag || saved?.version !== 1 || !saved.groups || typeof saved.groups !== 'object' || Array.isArray(saved.groups)) return;
          preferences = readPreferences(JSON.stringify(saved.groups));
          try { win.localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences)); } catch (_) { /* Storage is optional. */ }
          render();
        }).catch(() => { /* Local preferences remain usable offline. */ });
    }
  }

  return { STORAGE_KEY, CONFIG, normalize, readPreferences, fitSizes, resizePair, initialize };
});
