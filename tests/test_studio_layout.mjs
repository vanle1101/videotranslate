import test from 'node:test';
import assert from 'node:assert/strict';
import layout from '../static/studio-layout.js';

const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < .001, `${actual} ≠ ${expected}`);

test('invalid stored layouts fall back per group and valid ratios normalize', () => {
  const defaults = layout.readPreferences(null);
  for (const raw of ['{', 'null', '42', '"text"']) assert.deepEqual(layout.readPreferences(raw), defaults);
  const preferences = layout.readPreferences(JSON.stringify({columns: [2, 3, 5], left: [-1, 2], middle: [0, 1, 2], right: [1, 2, 3]}));
  assert.deepEqual(preferences.columns, [.2, .3, .5]);
  for (const key of ['left', 'middle', 'right']) assert.deepEqual(preferences[key], defaults[key]);
  assert.deepEqual(layout.normalize([Infinity, 1], [.4, .6]), [.4, .6]);
  assert.deepEqual(layout.normalize([1e308, 1e308], [.4, .6]), [.4, .6]);
});

test('old panel groups use new defaults while retaining column and left dimensions', () => {
  const preferences = layout.readPreferences(JSON.stringify({
    columns: [30, 40, 30], left: [35, 65], middle: [60, 40], right: [55, 27, 18],
  }));
  assert.deepEqual(preferences, {
    columns: [.3, .4, .3], left: [.35, .65], middle: [.60, .25, .15], right: [.55, .45],
  });
});

test('restored layouts respect minimum sizes after shrinking the window', () => {
  const sizes = layout.fitSizes(1000, [.01, .49, .5], [250, 360, 280]);
  assert.equal(sizes[0], 250);
  close(sizes.reduce((a, b) => a + b), 1000);
  assert.ok(sizes[1] >= 360 && sizes[2] >= 280);
  assert.deepEqual(layout.fitSizes(445, [.1, .8, .1], [250, 360, 280]), [125, 180, 140]);
});

test('moving a separator changes only its adjacent pair and clamps both extremes', () => {
  assert.deepEqual(layout.resizePair([300, 500, 350], 0, 50, [250, 360, 280]), [350, 450, 350]);
  assert.deepEqual(layout.resizePair([300, 500, 350], 0, Infinity, [250, 360, 280]), [440, 360, 350]);
  assert.deepEqual(layout.resizePair([300, 500, 350], 1, -Infinity, [250, 360, 280]), [300, 360, 490]);
});

function harness(raw = null, denyStorage = false, fetch = null) {
  class Element {
    constructor(dataset = {}) {
      this.dataset = dataset; this.children = []; this.style = {}; this.attributes = {}; this.listeners = {};
      this.clientWidth = 1220; this.clientHeight = 610; this.visible = true;
      const classes = new Set();
      this.classList = { add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name) };
    }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    emit(type, values = {}) { const event = {button: 0, pointerId: 1, clientX: 400, clientY: 300, preventDefault() {}, ...values}; (this.listeners[type] || []).forEach(fn => fn(event)); }
    setAttribute(name, value) { this.attributes[name] = value; }
    getClientRects() { return this.visible ? [{}] : []; }
    focus() { this.focused = true; }
    setPointerCapture(id) { this.captured = id; }
    hasPointerCapture(id) { return this.captured === id; }
    releasePointerCapture() { this.captured = null; this.emit('lostpointercapture'); }
  }
  const groups = Object.fromEntries(Object.entries(layout.CONFIG).map(([name, config]) => {
    const group = new Element({layoutGroup: name});
    for (let i = 0; i < config.defaults.length; i++) {
      group.children.push(new Element({layoutPanel: ''}));
      if (i < config.defaults.length - 1) group.children.push(new Element({layoutAxis: config.axis, layoutIndex: String(i)}));
    }
    return [name, group];
  }));
  const reset = new Element(); const html = new Element(); const viewport = new Element();
  const media = new Element(); media.matches = true;
  let stored = raw; let observer; const frames = []; const timers = new Map(); let timerId = 0;
  const doc = {documentElement: html, getElementById: id => id === 'studio-layout' ? groups.columns : id === 'btn-reset-layout' ? reset : null,
    querySelectorAll: () => Object.values(groups)};
  const win = {localStorage: {
    getItem: () => { if (denyStorage) throw Error('denied'); return stored; },
    setItem: (_, value) => { if (denyStorage) throw Error('denied'); stored = value; },
    removeItem: () => { if (denyStorage) throw Error('denied'); stored = null; },
  }, fetch, setTimeout: callback => { timers.set(++timerId, callback); return timerId; }, clearTimeout: id => timers.delete(id),
  matchMedia: () => media, getComputedStyle: () => ({}),
  requestAnimationFrame: callback => { frames.push(callback); return frames.length; },
  addEventListener: (...args) => viewport.addEventListener(...args),
  ResizeObserver: class { constructor(callback) { observer = callback; } observe() {} },
  };
  layout.initialize(doc, win);
  return {groups, reset, html, media, viewport, doc, win, saved: () => stored,
    observe: () => observer(), flush: () => { while (frames.length) frames.shift()(); },
    flushTimers: () => { const pending = [...timers.values()]; timers.clear(); pending.forEach(callback => callback()); },
    handle: (name, index = 0) => groups[name].children[index * 2 + 1]};
}

test('pointer resizing persists on completion and cancels back to the previous layout', () => {
  const ui = harness(); const handle = ui.handle('columns');
  handle.emit('pointerdown');
  handle.emit('pointermove', {clientX: 460});
  assert.equal(handle.attributes['aria-valuenow'], '360');
  assert.equal(ui.saved(), null);
  handle.emit('pointerup');
  assert.equal(JSON.parse(ui.saved()).columns[0], .3);
  assert.equal(ui.html.classList.contains('studio-layout-dragging'), false);
  const previous = ui.groups.columns.style.gridTemplateColumns;
  handle.emit('pointerdown'); handle.emit('pointermove', {clientX: 450}); handle.emit('pointercancel');
  assert.equal(ui.groups.columns.style.gridTemplateColumns, previous);
  assert.equal(handle.captured, null);
  assert.equal(JSON.parse(ui.saved()).columns[0], .3);
  const restored = harness(ui.saved());
  assert.equal(restored.handle('columns').attributes['aria-valuenow'], '360');
});

const settle = () => new Promise(resolve => setImmediate(resolve));

test('adjustment after a legacy server load saves the current panel counts', async () => {
  const requests = [];
  const ui = harness(null, false, async (_, options) => {
    if (options) requests.push(JSON.parse(options.body));
    return {ok: true, json: async () => ({version: 1, groups: {
      columns: [30, 40, 30], left: [35, 65], middle: [60, 40], right: [55, 27, 18],
    }})};
  });
  await settle();
  ui.handle('middle', 1).emit('keydown', {key: 'ArrowUp'});
  ui.flushTimers(); await settle();
  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0].groups.columns, [.3, .4, .3]);
  assert.deepEqual(requests[0].groups.left, [.35, .65]);
  assert.equal(requests[0].groups.middle.length, 3);
  assert.deepEqual(requests[0].groups.right, [.55, .45]);
});

test('server preferences survive origin changes and rapid adjustments use one debounced save', async () => {
  const requests = [];
  const ui = harness(null, false, async (url, options) => {
    requests.push({url, options});
    return {ok: true, json: async () => ({version: 1, groups: {columns: [.3, .4, .3]}})};
  });
  await settle();
  assert.equal(ui.handle('columns').attributes['aria-valuenow'], '360');
  ui.handle('columns').emit('keydown', {key: 'ArrowRight'});
  ui.handle('columns').emit('keydown', {key: 'ArrowRight'});
  ui.flushTimers(); await settle();
  assert.equal(requests.length, 2);
  assert.equal(requests[1].url, '/api/studio/layout');
  close(JSON.parse(requests[1].options.body).groups.columns[0], 380 / 1200);
  ui.reset.emit('click'); ui.flushTimers(); await settle();
  assert.deepEqual(JSON.parse(requests.at(-1).options.body), {version: 1, groups: {}});
});

test('a late server load cannot undo a user adjustment and network failures keep local sizing usable', async () => {
  let complete;
  const ui = harness(null, false, (_, options) => options ? Promise.reject(Error('offline')) : new Promise(resolve => { complete = resolve; }));
  const handle = ui.handle('columns');
  handle.emit('keydown', {key: 'ArrowRight'});
  complete({ok: true, json: async () => ({version: 1, groups: {columns: [.5, .3, .2]}})});
  await settle();
  assert.equal(handle.attributes['aria-valuenow'], '310');
  ui.flushTimers(); await settle();
  close(JSON.parse(ui.saved()).columns[0], 310 / 1200);
});

test('keyboard operates the correct axis, supports large steps and reset', () => {
  const ui = harness(); const handle = ui.handle('middle');
  ui.groups.middle.clientHeight = 1010; ui.observe(); ui.flush();
  assert.equal(handle.attributes['aria-orientation'], 'horizontal');
  const before = Number(handle.attributes['aria-valuenow']);
  handle.emit('keydown', {key: 'ArrowRight'});
  assert.equal(Number(handle.attributes['aria-valuenow']), before);
  handle.emit('keydown', {key: 'ArrowDown', shiftKey: true});
  assert.equal(Number(handle.attributes['aria-valuenow']), before + 50);
  handle.emit('keydown', {key: 'Home'});
  assert.equal(handle.attributes['aria-valuenow'], '220');
  ui.reset.emit('click');
  assert.equal(Number(handle.attributes['aria-valuenow']), before);
  assert.equal(ui.saved(), null);
});

test('mobile clears tracks, disables handles and restores desktop layout without losing preferences', () => {
  const ui = harness(); const handle = ui.handle('columns');
  handle.emit('keydown', {key: 'ArrowRight'});
  const desktopTracks = ui.groups.columns.style.gridTemplateColumns;
  ui.media.matches = false; ui.media.emit('change'); ui.flush();
  assert.equal(ui.groups.columns.style.gridTemplateColumns, '');
  assert.equal(ui.groups.middle.style.gridTemplateRows, '');
  assert.equal(handle.tabIndex, -1);
  handle.emit('keydown', {key: 'ArrowRight'});
  ui.media.matches = true; ui.media.emit('change'); ui.flush();
  assert.equal(ui.groups.columns.style.gridTemplateColumns, desktopTracks);
  assert.equal(handle.tabIndex, 0);
});

test('hidden tabs defer sizing, observer restores them, storage failure is harmless, init is idempotent', () => {
  const ui = harness(null, true); const group = ui.groups.right; const handle = ui.handle('right');
  group.visible = false; const before = group.style.gridTemplateRows;
  group.clientHeight = 900; ui.observe(); ui.flush();
  assert.equal(group.style.gridTemplateRows, before);
  group.visible = true; ui.observe(); ui.flush();
  assert.notEqual(group.style.gridTemplateRows, before);
  layout.initialize(ui.doc, ui.win);
  assert.equal(handle.listeners.keydown.length, 1);
  assert.doesNotThrow(() => handle.emit('keydown', {key: 'ArrowDown'}));
  assert.doesNotThrow(() => ui.reset.emit('click'));
});
