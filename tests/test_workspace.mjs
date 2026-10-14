import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

const source = readFileSync(new URL('../static/workspace.js', import.meta.url), 'utf8');
const key = 'studio.column-widths.v1';

function editor({ saved, blockedStorage = false } = {}) {
  const elements = new Map(), storage = new Map(saved === undefined ? [] : [[key, saved]]);
  const observers = [], resizeObservers = [], writes = [], reads = [], storageWrites = [];
  const frames = new Map(); let frameId = 0;
  let desktop = true;
  function el(id) {
    if (elements.has(id)) return elements.get(id);
    const listeners = new Map(), attrs = new Map(), classes = new Set(), css = new Map(), captures = new Set();
    const item = {
      id, hidden: false, disabled: false, value: '', title: '', dataset: {}, textContent: '', children: [], clientWidth: 1400,
      classList: { add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x) },
      style: { setProperty: (name, value) => { writes.push({id, name, value}); css.set(name, value); }, getPropertyValue: name => css.get(name), removeProperty: name => css.delete(name) },
      setAttribute: (name, value) => attrs.set(name, value), getAttribute: name => attrs.get(name),
      addEventListener: (name, fn) => listeners.set(name, [...(listeners.get(name) || []), fn]),
      emit(name, event = {}) { const e = { preventDefault() { this.prevented = true; }, ...event }; for (const fn of listeners.get(name) || []) fn(e); return e; },
      click() { if (!this.disabled) this.emit('click'); },
      focus() { this.focused = true; }, scrollIntoView() { this.scrolled = true; },
      setPointerCapture: id => captures.add(id), hasPointerCapture: id => captures.has(id), releasePointerCapture: id => captures.delete(id),
      getBoundingClientRect() {
        reads.push(id);
        const index = ['studio-controls', 'studio-preview', 'studio-sidebar'].indexOf(id);
        return { width: index < 0 ? 1400 : parseFloat(el('studio-workspace').style.getPropertyValue(`--column-${index}`)) };
      },
      querySelectorAll(selector) { return this.children.filter(child => child.classList?.contains(selector.slice(1))); },
    };
    elements.set(id, item);
    return item;
  }
  const document = { getElementById: el, addEventListener: (_, fn) => fn() };
  const window = el('window'); window.matchMedia = () => ({matches: desktop});
  const requestAnimationFrame = fn => { const id = ++frameId; frames.set(id, fn); return id; };
  const cancelAnimationFrame = id => frames.delete(id);
  Object.assign(window, {requestAnimationFrame, cancelAnimationFrame});
  vm.runInNewContext(source, {
    document, window, requestAnimationFrame, cancelAnimationFrame,
    getComputedStyle: () => ({paddingLeft: '16px', paddingRight: '16px'}),
    localStorage: {
      getItem: name => { if (blockedStorage) throw new Error('disabled'); return storage.get(name) ?? null; },
      setItem: (name, value) => { if (blockedStorage) throw new Error('disabled'); storageWrites.push({name, value}); storage.set(name, value); },
      removeItem: name => { if (blockedStorage) throw new Error('disabled'); storage.delete(name); },
    },
    ResizeObserver: class { constructor(fn) { resizeObservers.push(fn); } observe() {} },
    MutationObserver: class { constructor(fn) { this.fn = fn; } observe(target, options) { observers.push({target, options, fn: this.fn}); } },
  });
  return {
    el, storage, window, observers, writes, reads, storageWrites,
    pendingFrames: () => frames.size,
    flushFrame: () => { const batch = [...frames.values()]; frames.clear(); batch.forEach(fn => fn(16)); },
    resizeObserved: () => resizeObservers.forEach(fn => fn()),
    widths: () => [0, 1, 2].map(i => parseFloat(el('studio-workspace').style.getPropertyValue(`--column-${i}`))),
    desktop: value => { desktop = value; window.emit('resize'); },
    mutate: id => observers.filter(o => o.target.id === id).forEach(o => o.fn()),
  };
}
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < .001, `${actual} != ${expected}`);
function row(ui, id, original, translation) {
  const element = ui.el(id); element.classList.add('transcript-row');
  const source = ui.el(`${id}-source`); source.classList.add('transcript-original'); source.textContent = original;
  const translated = ui.el(`${id}-translated`); translated.classList.add('transcript-translation'); translated.textContent = translation;
  element.children = [source, translated];
  return element;
}

test('both separators resize only adjacent columns, respect minimums and persist across reload', () => {
  const ui = editor(), initial = ui.widths(), left = ui.el('resize-left');
  left.emit('pointerdown', {button: 0, pointerId: 1, clientX: 0});
  left.emit('pointermove', {pointerId: 1, clientX: 50});
  assert.deepEqual(ui.widths(), initial, 'drag moves a composited guide without reflowing media/transcript');
  left.emit('pointerup', {pointerId: 1});
  const widths = ui.widths();
  close(widths[0], initial[0] + 50); close(widths[1], initial[1] - 50); close(widths[2], initial[2]);
  const restored = editor({saved: ui.storage.get(key)});
  restored.widths().forEach((w, i) => close(w, widths[i]));
  const right = ui.el('resize-right');
  right.emit('pointerdown', {button: 0, pointerId: 2, clientX: 0});
  right.emit('pointermove', {pointerId: 2, clientX: 5000});
  right.emit('pointerup', {pointerId: 2});
  close(ui.widths()[0], widths[0]); close(ui.widths()[2], 300);
  close(ui.widths().reduce((a, b) => a + b), 1344);
  assert.equal(right.getAttribute('aria-valuenow'), '300');
});

test('Escape and pointercancel restore initial widths without saving the interrupted drag', () => {
  for (const cancel of ['Escape', 'pointercancel']) {
    const ui = editor(), before = ui.widths(), handle = ui.el('resize-left');
    handle.emit('pointerdown', {button: 0, pointerId: 1, clientX: 0});
    handle.emit('pointermove', {pointerId: 1, clientX: 70});
    if (cancel === 'Escape') handle.emit('keydown', {key: 'Escape'});
    else handle.emit(cancel, {pointerId: 1});
    assert.deepEqual(ui.widths(), before); assert.equal(ui.storage.has(key), false);
    assert.equal(ui.el('studio-workspace').classList.contains('is-resizing'), false);
    assert.equal(handle.hasPointerCapture(1), false);
  }
});

test('viewport switch cancels drag and preserves saved desktop proportions', () => {
  const ui = editor(); ui.el('resize-left').emit('keydown', {key: 'ArrowRight'});
  const saved = ui.storage.get(key), before = ui.widths(), h = ui.el('resize-right');
  h.emit('pointerdown', {button: 0, pointerId: 1, clientX: 0});
  h.emit('pointermove', {pointerId: 1, clientX: 80});
  ui.desktop(false); h.emit('pointerup', {pointerId: 1});
  assert.equal(ui.storage.get(key), saved); assert.equal(h.hasPointerCapture(1), false);
  const event = h.emit('keydown', {key: 'ArrowRight'}); assert.equal(event.prevented, undefined);
  ui.desktop(true); ui.widths().forEach((w, i) => close(w, before[i]));
});

test('second pointer cannot hijack a drag and unrelated pointer release is ignored', () => {
  const ui = editor(), h = ui.el('resize-left'), other = ui.el('resize-right');
  h.emit('pointerdown', {button: 0, pointerId: 1, clientX: 0});
  other.emit('pointerdown', {button: 0, pointerId: 2, clientX: 0});
  other.emit('pointerup', {pointerId: 2});
  assert.equal(h.hasPointerCapture(1), true); assert.equal(other.hasPointerCapture(2), false);
  h.emit('pointermove', {pointerId: 1, clientX: 15}); ui.window.emit('blur');
  assert.equal(h.hasPointerCapture(1), false); assert.ok(ui.storage.has(key));
});

test('keyboard resizing, Shift step and Home/reset restore defaults', () => {
  const ui = editor(), before = ui.widths(), h = ui.el('resize-left');
  h.emit('keydown', {key: 'ArrowRight', shiftKey: true}); close(ui.widths()[0], before[0] + 50);
  h.emit('keydown', {key: 'ArrowLeft'}); close(ui.widths()[0], before[0] + 40);
  h.emit('keydown', {key: 'Home'}); assert.deepEqual(ui.widths(), before); assert.equal(ui.storage.has(key), false);
  h.emit('keydown', {key: 'ArrowRight'}); ui.el('btn-layout-reset').click(); assert.deepEqual(ui.widths(), before);
});

test('corrupt or unavailable storage cannot break resize or produce invalid dimensions', () => {
  const expected = editor().widths();
  for (const saved of ['garbage', '{}', '[1,2]', '[0,0,0]', '[-1,1,1]', '[1e309,2,3]', '[1e308,1e308,1e308]']) {
    assert.deepEqual(editor({saved}).widths(), expected);
  }
  const ui = editor({blockedStorage: true}); ui.el('resize-left').emit('keydown', {key: 'ArrowRight'});
  close(ui.widths()[0], expected[0] + 10);
});

test('tab navigation updates selection, panel visibility, focus and transcript mode', () => {
  const ui = editor(); ui.el('editor-tab-translation').emit('keydown', {key: 'ArrowRight'});
  assert.equal(ui.el('editor-transcript').dataset.mode, 'original');
  assert.equal(ui.el('editor-tab-original').focused, true);
  ui.el('editor-tab-original').emit('keydown', {key: 'End'});
  assert.equal(ui.el('editor-transcript').hidden, true); assert.equal(ui.el('editor-advanced').hidden, false);
  ui.el('editor-tab-advanced').emit('keydown', {key: 'ArrowRight'});
  assert.equal(ui.el('editor-tab-translation').getAttribute('aria-selected'), 'true');
  assert.equal(ui.el('editor-transcript').hidden, false); assert.equal(ui.el('editor-advanced').hidden, true);
});

test('search matches active language and Vietnamese accents; editing rows stay visible until editing ends', () => {
  const ui = editor(), search = ui.el('transcript-search');
  const first = row(ui, 'row1', '原文', 'Đừng lo, mẹ đây.'), second = row(ui, 'row2', '另一句', 'Xin chào.');
  ui.el('segments-list').children = [first, second];
  search.value = 'dung lo'; search.emit('input'); assert.equal(first.hidden, false); assert.equal(second.hidden, true);
  second.dataset.editing = 'true'; ui.mutate('segments-list'); assert.equal(second.hidden, false);
  second.dataset.editing = 'false'; ui.mutate('segments-list'); assert.equal(second.hidden, true);
  search.value = '另一句'; search.emit('input'); assert.equal(first.hidden, true); assert.equal(second.hidden, true);
  ui.el('editor-tab-original').click(); assert.equal(first.hidden, true); assert.equal(second.hidden, false);
  search.value = 'xin chao'; search.emit('input'); assert.equal(second.hidden, true);
  ui.el('editor-tab-translation').click(); assert.equal(second.hidden, false);
  search.value = 'not found'; search.emit('input'); assert.equal(ui.el('transcript-search-empty').hidden, false);
  search.emit('keydown', {key: 'Escape'}); assert.equal(search.value, ''); assert.equal(first.hidden, false); assert.equal(second.hidden, false);
});

test('toolbar targets real captions, audio and voice controls; preview mirrors start availability', () => {
  const ui = editor(); ui.el('tool-captions').click(); assert.equal(ui.el('caption-style-panel').open, true);
  assert.equal(ui.el('editor-advanced').hidden, false);
  ui.el('tool-audio').click(); assert.equal(ui.el('vol-dub').focused, true);
  ui.el('tool-voice').click(); assert.equal(ui.el('voice-search').focused, true);
  let starts = 0; const start = ui.el('btn-start'); start.addEventListener('click', () => starts++);
  ui.el('btn-source-preview').click(); assert.equal(starts, 1);
  start.disabled = true; ui.mutate('btn-start'); ui.el('btn-source-preview').click(); assert.equal(starts, 1);
  start.disabled = false; start.classList.add('hidden'); ui.mutate('btn-start'); assert.equal(ui.el('btn-source-preview').disabled, true);
});

test('native saved widths survive a new browser origin and edits persist to native settings', () => {
  const ui = editor(); let callback, saved, resets = 0;
  const bridge = {getColumnWidths: fn => { callback = fn; }, setColumnWidths: raw => { saved = raw; }, resetColumnWidths: () => resets++};
  ui.window.restoreStudioLayout(bridge); callback('[0.3,0.4,0.3]');
  close(ui.widths()[0], 1344 * .3);
  ui.el('resize-left').emit('keydown', {key: 'ArrowRight'});
  assert.equal(saved, ui.storage.get(key));
  ui.el('btn-layout-reset').click(); assert.equal(resets, 1);
});

test('late native preference cannot overwrite user changes or an active drag', () => {
  for (const operation of ['resize', 'drag', 'reset']) {
    const ui = editor(); let callback;
    ui.window.restoreStudioLayout({getColumnWidths: fn => { callback = fn; }});
    if (operation === 'resize') ui.el('resize-left').emit('keydown', {key: 'ArrowRight'});
    if (operation === 'drag') ui.el('resize-left').emit('pointerdown', {button: 0, pointerId: 1, clientX: 0});
    if (operation === 'reset') ui.el('btn-layout-reset').click();
    const before = ui.widths(); callback('[0.4,0.3,0.3]'); assert.deepEqual(ui.widths(), before);
  }
  const ui = editor(); ui.el('resize-left').emit('keydown', {key: 'ArrowRight'});
  let saved; ui.window.restoreStudioLayout({getColumnWidths: () => { throw Error('must not load'); }, setColumnWidths: raw => { saved = raw; }});
  assert.equal(saved, ui.storage.get(key));
});

test('high-frequency drag coalesces guide paint per frame and performs no column reflow before release', () => {
  const ui = editor(), handle = ui.el('resize-left'), initial = ui.widths();
  handle.emit('pointerdown', {button: 0, pointerId: 8, clientX: 400});
  ui.writes.length = 0; ui.reads.length = 0;
  for (let delta = 1; delta <= 90; delta++) handle.emit('pointermove', {pointerId: 8, clientX: 400 + delta});
  assert.deepEqual(ui.widths(), initial);
  assert.equal(ui.reads.length, 0, 'pointermove must not remeasure column geometry');
  assert.equal(ui.writes.length, 0, 'guide paints are deferred to one animation frame');
  assert.equal(ui.pendingFrames(), 1);
  ui.resizeObserved();
  assert.equal(ui.writes.length, 0, 'ResizeObserver must not rewrite layout mid-drag');
  ui.flushFrame();
  assert.equal(ui.writes.length, 1);
  assert.equal(handle.style.getPropertyValue('transform'), 'translateX(90px)');
  assert.equal(ui.storageWrites.length, 0);
  handle.emit('pointerup', {pointerId: 8});
  close(ui.widths()[0], initial[0] + 90);
  close(ui.widths()[1], initial[1] - 90);
  assert.equal(ui.storageWrites.length, 1);
  assert.equal(handle.style.getPropertyValue('transform'), undefined);
  assert.equal(ui.pendingFrames(), 0);
});

test('release before guide frame paints commits final movement once without a stale guide', () => {
  const ui = editor(), handle = ui.el('resize-right'), before = ui.widths();
  handle.emit('pointerdown', {button: 0, pointerId: 4, clientX: 700});
  handle.emit('pointermove', {pointerId: 4, clientX: 740});
  handle.emit('pointerup', {pointerId: 4});
  close(ui.widths()[1], before[1] + 40); close(ui.widths()[2], before[2] - 40);
  assert.equal(ui.storageWrites.length, 1);
  ui.flushFrame();
  assert.equal(handle.style.getPropertyValue('transform'), undefined);
  handle.emit('lostpointercapture', {pointerId: 4});
  assert.equal(ui.storageWrites.length, 1, 'capture release must not store a second result');
});

test('cancel before guide frame cannot paint stale movement or overwrite persisted widths', () => {
  for (const cancelledBy of ['pointercancel', 'Escape', 'viewport']) {
    const ui = editor(), handle = ui.el('resize-left');
    handle.emit('keydown', {key: 'ArrowRight'});
    const saved = ui.storage.get(key), before = ui.widths(), count = ui.storageWrites.length;
    handle.emit('pointerdown', {button: 0, pointerId: 2, clientX: 500});
    handle.emit('pointermove', {pointerId: 2, clientX: 555});
    if (cancelledBy === 'Escape') handle.emit('keydown', {key: 'Escape'});
    else if (cancelledBy === 'viewport') ui.desktop(false);
    else handle.emit('pointercancel', {pointerId: 2});
    ui.flushFrame();
    assert.equal(handle.style.getPropertyValue('transform'), undefined);
    assert.equal(ui.storage.get(key), saved);
    assert.equal(ui.storageWrites.length, count);
    assert.deepEqual(ui.widths(), before);
    assert.equal(handle.hasPointerCapture(2), false);
    assert.equal(ui.el('studio-workspace').classList.contains('is-resizing'), false);
  }
});

test('switching transcript language refilters existing query and keeps tab semantics consistent', () => {
  const ui = editor(), sourceTab = ui.el('editor-tab-original'), translatedTab = ui.el('editor-tab-translation');
  const first = row(ui, 'first', '妈妈', 'Mẹ ở đây'), second = row(ui, 'second', '爸爸', 'Bố đang về');
  ui.el('segments-list').children = [first, second];
  const search = ui.el('transcript-search');
  search.value = '妈妈'; search.emit('input');
  assert.equal(first.hidden, true); assert.equal(ui.el('transcript-search-empty').hidden, false);
  sourceTab.click();
  assert.equal(first.hidden, false); assert.equal(second.hidden, true);
  assert.equal(ui.el('transcript-search-empty').hidden, true);
  assert.equal(sourceTab.getAttribute('aria-selected'), 'true'); assert.equal(sourceTab.tabIndex, 0);
  assert.equal(translatedTab.getAttribute('aria-selected'), 'false'); assert.equal(translatedTab.tabIndex, -1);
  assert.equal(ui.el('editor-transcript').getAttribute('aria-labelledby'), 'editor-tab-original');
  translatedTab.click();
  assert.equal(first.hidden, true);
  assert.equal(ui.el('editor-transcript').getAttribute('aria-labelledby'), 'editor-tab-translation');
  search.value = 've'; search.emit('input'); assert.equal(second.hidden, false);
  ui.el('second-translated').textContent = 'Bố ở nhà'; ui.mutate('segments-list');
  assert.equal(second.hidden, true, 'new translation must obey the current-language filter');
  first.dataset.editing = 'true'; ui.mutate('segments-list');
  assert.equal(first.hidden, false, 'a live edit cannot be hidden by worker updates');
  first.dataset.editing = 'false'; ui.mutate('segments-list'); assert.equal(first.hidden, true);
});

