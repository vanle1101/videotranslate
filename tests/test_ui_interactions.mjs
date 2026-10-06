import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

const source = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
const template = readFileSync(new URL('../templates/index.html', import.meta.url), 'utf8');
const stylesheet = readFileSync(new URL('../static/style.css', import.meta.url), 'utf8');

function studio(cookieReply = { configured: false, count: 0, message: '' }, voiceConfig = {}) {
  const elements = new Map(), audio = [], sockets = [], requests = [], alerts = [], copied = [];
  const intervals = new Map();
  const timeouts = new Map();
  let nextIntervalId = 1;
  let ready;
  class Element {
    constructor(id = '') {
      this.id = id; this.events = {}; this.children = []; this.style = {}; this.dataset = {};
      this.style.setProperty = (name, value) => { this.style[name] = value; };
      this.value = ''; this.files = []; this.checked = true;
      this.paused = true; this.currentTime = 0; this.playbackRate = 1; this.ended = false;
      this.volume = 1; this.muted = false;
      this.playCount = 0; this.classes = new Set(); this.hidden = false; this.disabled = false;
      this.classList = {
        add: (...names) => names.forEach(name => this.classes.add(name)),
        remove: (...names) => names.forEach(name => this.classes.delete(name)),
        contains: name => this.classes.has(name),
        toggle: (name, enabled) => enabled ? this.classes.add(name) : this.classes.delete(name),
      };
    }
    addEventListener(name, callback) { (this.events[name] ||= []).push(callback); }
    async emit(name, data = {}) {
      // Native DOM dispatch invokes every listener synchronously. Awaiting each
      // listener separately would defer pause handlers behind unrelated ones.
      const pending = (this.events[name] || []).map(callback => callback({ target: this, preventDefault() {}, stopPropagation() {}, ...data }));
      await Promise.all(pending);
    }
    set innerHTML(value) {
      this.html = value; this.children = [];
      for (const match of value.matchAll(/id="([^"]+)"/g)) elements.set(match[1], new Element(match[1]));
    }
    get innerHTML() { return this.html || ''; }
    get className() { return [...this.classes].join(' '); }
    set className(value) { this.classes = new Set(value.split(/\s+/).filter(Boolean)); }
    appendChild(child) { child.parentElement = this; this.children.push(child); if (child.id) elements.set(child.id, child); return child; }
    append(...children) { children.forEach(child => this.appendChild(child)); }
    replaceChildren(...children) { this.children = []; children.forEach(child => this.appendChild(child)); this.options = this.children; }
    add(option) { this.options.push(option); }
    setAttribute(name, value) {
      if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = String(value);
      else this[name === 'class' ? 'className' : name] = String(value);
    }
    getAttribute(name) { return this[name]; }
    removeAttribute(name) { delete this[name]; }
    async click() { await this.emit('click'); }
    focus() { document.activeElement = this; }
    setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
    load() { this.currentTime = 0; this.loadCount = (this.loadCount || 0) + 1; }
    pause() { this.paused = true; this.emit('pause'); }
    async play() { this.paused = false; this.playCount++; await this.emit('play'); }
    async requestFullscreen() { this.fullscreenCount = (this.fullscreenCount || 0) + 1; document.fullscreenElement = this; }
    getBoundingClientRect() { return { left: 0, width: 100 }; }
    matches(selector) {
      const attribute = selector.match(/\[([\w-]+)(?:="([^"]*)")?\]/);
      const simple = selector.replace(/\[[^\]]+\]/g, '');
      if (simple.startsWith('.') && !this.classes.has(simple.slice(1))) return false;
      if (simple.startsWith('#') && this.id !== simple.slice(1)) return false;
      if (simple && !/^[.#]/.test(simple) && this.tagName !== simple.toUpperCase()) return false;
      if (attribute) {
        const key = attribute[1].slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase());
        const value = attribute[1].startsWith('data-') ? this.dataset[key] : this[attribute[1]];
        if (value === undefined || (attribute[2] !== undefined && value !== attribute[2])) return false;
      }
      return true;
    }
    querySelectorAll(selector) {
      const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
      return selector === '*' ? descendants : descendants.filter(child => selector.split(',').some(part => child.matches(part.trim())));
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
    scrollIntoView() {}
    getClientRects() { return [1]; }
    remove() {}
  }
  for (const match of template.matchAll(/<([a-z][\w-]*)\b[^>]*\bid="([^"]+)"/gi)) {
    const element = new Element(match[2]);
    element.tagName = match[1].toUpperCase();
    if (element.tagName === 'SELECT') element.options = [];
    elements.set(match[2], element);
  }
  const el = id => elements.get(id);
  // Match these intentional image-preserving defaults in the actual template.
  // Server-provided caption_layout is the primary visual contract.  Position
  // mode is enabled by default; legacy masking remains an inaccessible stub.
  el('toggle-mask-chinese').checked = false;
  el('toggle-mask-chinese').disabled = true;
  el('toggle-screen-text').checked = true;
  const playerSurface = new Element();
  playerSurface.className = 'player-surface';
  playerSurface.appendChild(el('player-container'));
  for (const [id, value] of Object.entries({ 'vol-dub': '1', 'vol-bgm': '0.3', 'player-volume': '1', 'buffer-select': '10', 'voice-select': 'vi-VN-HoaiMyNeural' })) el(id).value = value;
  const documentEvents = {};
  const document = {
    getElementById: el,
    createElement: tag => { const element = new Element(); element.tagName = tag.toUpperCase(); return element; },
    querySelectorAll: selector => selector === '.source-tab' ? ['source-tab-url', 'source-tab-file', 'source-tab-library'].map(el) : [],
    addEventListener: (event, callback) => {
      if (event === 'DOMContentLoaded') ready = callback;
      else (documentEvents[event] ||= []).push(callback);
    },
    async exitFullscreen() { this.fullscreenElement = null; },
    body: new Element(), activeElement: null,
  };
  document.body.dataset = { asrEngine: 'faster-whisper', ttsEngine: 'edge-tts' };
  class Audio extends Element { constructor(url) { super(); this.src = url; audio.push(this); } }
  class WebSocket {
    static OPEN = 1;
    constructor() { this.readyState = 1; this.sent = []; sockets.push(this); }
    close() { this.readyState = 3; }
    send(message) { this.sent.push(JSON.parse(message)); }
    receive(message) { this.onmessage({ data: JSON.stringify(message) }); }
  }
  const replies = new Map([
    ['/api/hardware', {}], ['/api/tasks', { tasks: [] }],
    ['/api/settings', { buffer_target: '15', ducking_level: '-18', opencode_free_models: [], gemini_model: 'gemini-fixture' }],
    ['/api/muse/status', {}],
    ['/api/douyin/cookies', cookieReply],
    ['/api/voices', voiceConfig.catalog || {}],
  ]);
  const windowEvents = {}, saved = new Map(Object.entries(voiceConfig.saved || {}));
  const window = {
    location: { protocol: 'http:', host: 'localhost' },
    addEventListener(name, callback) { (windowEvents[name] ||= []).push(callback); },
    async emit(name) { for (const callback of windowEvents[name] || []) await callback(); },
  };
  const fetch = async (url, options = {}) => {
    requests.push({ url, options });
    const reply = replies.get(url) || {};
    if (typeof reply === 'function') return reply(options);
    return { ok: !reply.failure, json: async () => reply };
  };
  vm.runInNewContext(source, {
    document, window, Audio, WebSocket, fetch, console, AbortController, alert: value => alerts.push(value),
    Option: function(text, value) { this.text = text; this.value = value; },
    FormData: class {
      constructor() { this.parts = []; }
      append(name, value, filename) { this.parts.push({ name, value, filename }); }
    },
    URL: Object.assign(class extends URL {}, { createObjectURL: () => 'blob:video', revokeObjectURL() {} }),
    navigator: { clipboard: { writeText: async text => { copied.push(text); } } },
    localStorage: { removeItem(key) { saved.delete(key); }, getItem(key) { return saved.get(key); }, setItem(key, value) { saved.set(key, value); } },
    setTimeout(callback, delay) { const id = nextIntervalId++; timeouts.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timeouts.delete(id); },
    setInterval(callback, delay) {
      const id = nextIntervalId++;
      intervals.set(id, { callback, delay });
      return id;
    },
    clearInterval(id) { intervals.delete(id); },
  });
  ready();
  const flush = () => new Promise(resolve => setImmediate(resolve));
  async function tickIntervals(delay) {
    await flush();
    for (const timer of [...intervals.values()]) {
      if (timer.delay === delay) await timer.callback();
    }
    await flush();
  }
  async function tickTimeouts(delay) {
    for (const [id, timer] of [...timeouts]) {
      if (timer.delay === delay) { timeouts.delete(id); timer.callback(); }
    }
    await flush();
  }
  async function start() {
    await flush();
    window.loadDroppedLocalVideo('D:/clip.mp4');
    replies.set('/api/streaming/start-local-file', { task_id: 'fixture', video_url: '/fixture.mp4' });
    await el('btn-start').click();
    sockets.at(-1).receive({ type: 'init', duration: 10, segments_count: 1, bgm_url: '/bgm.m4a',
      segments: [{ id: 0, start: 0, end: 10, duration: 10, status: 'READY', audio_url: '/dub.wav', final_vi: '<b>Xin chào</b>' }] });
  }
  const row = id => el('voice-list').querySelectorAll('.voice-row').find(item => item.dataset.voiceId === id);
  const choose = id => row(id).querySelector('.voice-choose-button').click();
  const preview = id => row(id).querySelector('.voice-preview-button').click();
  return { el, row, choose, preview, audio, sockets, requests, replies, alerts, copied, window, document, playerSurface, flush, tickIntervals, tickTimeouts, start, saved };
}

const voiceCatalog = {
  default_voice_id: 'edge:vi-VN-HoaiMyNeural',
  voices: [
    { id: 'edge:vi-VN-HoaiMyNeural', name: 'Hoài My (Nữ)', engine: 'edge-tts', source: 'Microsoft Edge', language: 'vi-VN', available: true, offline: false },
    { id: 'vieneu:Trúc Ly', name: 'Trúc Ly', engine: 'vieneu-tts', source: 'VieNeu · Hugging Face', language: 'vi-VN', description: 'Nữ miền Bắc', available: true, offline: true },
    { id: 'vieneu:Unavailable', name: 'Giọng chưa tải', engine: 'vieneu-tts', source: 'VieNeu · Hugging Face', language: 'vi-VN', available: false, offline: true },
  ],
};

test('voice catalog renders a list with source groups, restores selection and offers per-row listening', async () => {
  const ui = studio(undefined, { catalog: voiceCatalog, saved: { 'studio.voice-id': 'vieneu:Trúc Ly' } });
  await ui.flush();
  assert.equal(ui.el('voice-select').tagName, 'INPUT');
  assert.doesNotMatch(template, /<select[^>]+id="voice-select"/);
  assert.equal(ui.el('voice-select').value, 'vieneu:Trúc Ly');
  assert.deepEqual(ui.el('voice-list').querySelectorAll('.voice-group').map(group => group.dataset.source), ['Microsoft Edge', 'VieNeu · Hugging Face']);
  assert.equal(ui.el('voice-list').querySelectorAll('.voice-row').length, voiceCatalog.voices.length);
  assert.equal(ui.row('vieneu:Unavailable').querySelector('.voice-choose-button').disabled, true);
  assert.equal(ui.row('vieneu:Unavailable').querySelector('.voice-preview-button').disabled, true);
  assert.match(ui.el('voice-source').textContent, /Trúc Ly · VieNeu · Hugging Face · Chạy trên máy/);
  assert.match(ui.row('vieneu:Trúc Ly').querySelector('.voice-description').textContent, /Nữ miền Bắc/);
  assert.equal(ui.row('vieneu:Trúc Ly').querySelector('.voice-choose-button').getAttribute('aria-pressed'), 'true');
  assert.equal(ui.row('vieneu:Trúc Ly').querySelector('.voice-preview-button').disabled, false);
  assert.equal(ui.el('voice-preview-audio').playCount, 0);
  assert.equal(ui.requests.filter(req => req.url === '/api/voices/preview').length, 0);
  await ui.choose('edge:vi-VN-HoaiMyNeural');
  assert.equal(ui.saved.get('studio.voice-id'), 'edge:vi-VN-HoaiMyNeural');
  assert.equal(ui.row('edge:vi-VN-HoaiMyNeural').querySelector('.voice-choose-button').getAttribute('aria-pressed'), 'true');
  assert.equal(ui.row('vieneu:Trúc Ly').querySelector('.voice-choose-button').getAttribute('aria-pressed'), 'false');
  assert.match(ui.el('voice-source').textContent, /Microsoft Edge · Cần Internet/);
});

test('missing or unavailable saved voice falls back to the catalog default', async () => {
  for (const savedId of ['removed:voice', 'vieneu:Unavailable']) {
    const ui = studio(undefined, { catalog: voiceCatalog, saved: { 'studio.voice-id': savedId } });
    await ui.flush();
    assert.equal(ui.el('voice-select').value, voiceCatalog.default_voice_id);
    assert.equal(ui.row(voiceCatalog.default_voice_id).querySelector('.voice-preview-button').disabled, false);
  }
});

test('voice search matches accents, descriptions and sources without altering selection', async () => {
  const ui = studio(undefined, { catalog: voiceCatalog }); await ui.flush();
  const shown = () => ui.el('voice-list').querySelectorAll('.voice-row')
    .filter(row => !row.hidden && !row.classList.contains('hidden') && row.style.display !== 'none')
    .map(row => row.dataset.voiceId);
  for (const query of ['truc ly', 'NỮ MIỀN BẮC']) {
    ui.el('voice-search').value = query;
    await ui.el('voice-search').emit('input');
    assert.deepEqual(shown(), ['vieneu:Trúc Ly']);
    assert.equal(ui.el('voice-select').value, voiceCatalog.default_voice_id);
  }
  ui.el('voice-search').value = 'hugging face'; await ui.el('voice-search').emit('input');
  assert.deepEqual(shown(), ['vieneu:Trúc Ly', 'vieneu:Unavailable']);
  ui.el('voice-search').value = 'no-matching-voice'; await ui.el('voice-search').emit('input');
  assert.deepEqual(shown(), []);
  ui.el('voice-search').value = ''; await ui.el('voice-search').emit('input');
  assert.equal(shown().length, voiceCatalog.voices.length);
  assert.equal(ui.requests.filter(req => req.url === '/api/voices/preview').length, 0);
});

test('selected catalog voice id is sent on URL, local and upload starts without conflicting legacy engine', async () => {
  for (const path of ['url', 'local-file', 'upload']) {
    const ui = studio(undefined, { catalog: voiceCatalog }); await ui.flush();
    await ui.choose('vieneu:Trúc Ly');
    if (path === 'url') {
      ui.el('video-url').value = 'https://v.douyin.com/example/';
      await ui.el('video-url').emit('input');
    } else if (path === 'local-file') ui.window.loadDroppedLocalVideo('D:/clip.mp4');
    else {
      ui.el('video-file').files = [{ name: 'clip.mp4', size: 100 }];
      await ui.el('video-file').emit('change');
    }
    const endpoint = `/api/streaming/start-${path}`;
    ui.replies.set(endpoint, { task_id: 'catalog-fixture', video_url: null });
    await ui.el('btn-start').click();
    const request = ui.requests.find(req => req.url === endpoint);
    const body = path === 'upload' ? Object.fromEntries(request.options.body.parts.map(part => [part.name, part.value])) : JSON.parse(request.options.body);
    assert.equal(body.voice_id, 'vieneu:Trúc Ly');
    assert.equal(body.voice, undefined);
    assert.equal(body.tts_engine, undefined);
    assert.equal(ui.el('voice-select').disabled, true);
    for (const row of ui.el('voice-list').querySelectorAll('.voice-row')) {
      assert.equal(row.querySelector('.voice-preview-button').disabled, true);
      assert.equal(row.querySelector('.voice-choose-button').disabled, true);
    }
  }
});

test('failed voice catalog retains legacy Edge selection and its working start payload', async () => {
  const ui = studio(); await ui.flush();
  assert.equal(ui.el('voice-select').value, 'vi-VN-HoaiMyNeural');
  assert.match(ui.el('voice-catalog-status').textContent, /Chưa tải được danh sách mở rộng/);
  assert.equal(ui.el('voice-list').querySelectorAll('.voice-row').length, 2);
  assert.equal(ui.el('voice-list').querySelectorAll('.voice-preview-button').some(button => !button.disabled), false);
  await ui.start();
  const body = JSON.parse(ui.requests.find(req => req.url === '/api/streaming/start-local-file').options.body);
  assert.equal(body.voice, 'vi-VN-HoaiMyNeural');
  assert.equal(body.tts_engine, 'edge-tts');
  assert.equal(body.voice_id, undefined);
});

test('unavailable catalog selection cannot fall through to a mismatched legacy engine', async () => {
  const ui = studio(undefined, { catalog: voiceCatalog }); await ui.flush();
  ui.window.loadDroppedLocalVideo('D:/clip.mp4');
  ui.el('voice-select').value = 'vieneu:Unavailable';
  await ui.el('btn-start').click();
  assert.match(ui.el('voice-preview-status').textContent, /chọn một giọng đang có sẵn/);
  assert.equal(ui.requests.some(req => req.url.includes('/streaming/start')), false);
});

test('row preview auditions an unselected voice, prevents duplicates and ignores a late result after choosing', async () => {
  const ui = studio(undefined, { catalog: voiceCatalog }); await ui.flush();
  let finish;
  ui.replies.set('/api/voices/preview', () => new Promise(resolve => { finish = resolve; }));
  const pending = ui.preview('vieneu:Trúc Ly'); await ui.flush();
  assert.equal(ui.row('vieneu:Trúc Ly').querySelector('.voice-preview-button').disabled, true);
  assert.match(ui.el('voice-preview-status').textContent, /Đang tạo mẫu.*Trúc Ly/);
  assert.equal(ui.el('voice-select').value, voiceCatalog.default_voice_id);
  assert.notEqual(ui.saved.get('studio.voice-id'), 'vieneu:Trúc Ly');
  await ui.preview('vieneu:Trúc Ly');
  assert.equal(ui.requests.filter(req => req.url === '/api/voices/preview').length, 1);
  const body = JSON.parse(ui.requests.find(req => req.url === '/api/voices/preview').options.body);
  assert.deepEqual(body, { voice_id: 'vieneu:Trúc Ly' });
  await ui.choose('vieneu:Trúc Ly');
  finish({ ok: true, json: async () => ({ preview_id: 'late', audio_url: '/api/voices/preview/late/audio' }) });
  await pending;
  assert.equal(ui.el('voice-preview-audio').playCount, 0);
  assert.equal(ui.el('voice-preview-audio').src, undefined);
  assert.ok(ui.requests.some(req => req.url === '/api/voices/preview/late' && req.options.method === 'DELETE'));
  assert.equal(ui.el('voice-preview-status').textContent, '');
});

test('successful voice preview uses controls, reports playback errors and releases its file on pagehide', async () => {
  const ui = studio(undefined, { catalog: voiceCatalog }); await ui.flush();
  ui.replies.set('/api/voices/preview', { preview_id: 'preview-1', audio_url: '/api/voices/preview/preview-1/audio' });
  await ui.preview('vieneu:Trúc Ly');
  assert.equal(ui.el('voice-preview-audio').src, 'http://localhost/api/voices/preview/preview-1/audio');
  assert.equal(ui.el('voice-preview-audio').playCount, 1);
  assert.equal(ui.el('voice-preview-audio').classList.contains('hidden'), false);
  assert.match(ui.el('voice-preview-status').textContent, /Đang nghe mẫu: Trúc Ly/);
  assert.match(ui.el('voice-preview-audio').getAttribute('aria-label'), /Trúc Ly/);
  assert.equal(ui.el('voice-select').value, voiceCatalog.default_voice_id);
  assert.notEqual(ui.saved.get('studio.voice-id'), 'vieneu:Trúc Ly');
  await ui.el('voice-preview-audio').emit('error');
  assert.match(ui.el('voice-preview-status').textContent, /Không phát được mẫu giọng/);
  assert.equal(ui.row('vieneu:Trúc Ly').querySelector('.voice-preview-button').title, 'Nghe thử');
  assert.match(ui.row('vieneu:Trúc Ly').querySelector('.voice-preview-button').getAttribute('aria-label'), /Nghe thử giọng Trúc Ly/);
  assert.match(ui.row('vieneu:Trúc Ly').querySelector('.voice-preview-button').innerHTML, /fa-play/);
  assert.equal(ui.row('vieneu:Trúc Ly').dataset.previewing, 'false');
  assert.equal(ui.el('voice-preview-audio').paused, true);
  assert.equal(ui.el('voice-preview-audio').src, undefined);
  assert.ok(ui.requests.some(req => req.url === '/api/voices/preview/preview-1' && req.options.method === 'DELETE'));
  ui.replies.set('/api/voices/preview', { preview_id: 'preview-retry', audio_url: '/api/voices/preview/preview-retry/audio' });
  await ui.preview('vieneu:Trúc Ly');
  assert.equal(ui.requests.filter(req => req.url === '/api/voices/preview' && req.options.method === 'POST').length, 2);
  assert.equal(ui.el('voice-preview-audio').src, 'http://localhost/api/voices/preview/preview-retry/audio');
  assert.equal(ui.el('voice-preview-audio').playCount, 2);
  assert.equal(ui.el('voice-select').value, voiceCatalog.default_voice_id);
  await ui.window.emit('pagehide');
  assert.equal(ui.el('voice-preview-audio').paused, true);
  assert.equal(ui.el('voice-preview-audio').src, undefined);
  assert.ok(ui.requests.some(req => req.url === '/api/voices/preview/preview-retry' && req.options.method === 'DELETE' && req.options.keepalive));
});

test('same-row preview button stops audio and choosing another row keeps sample controls synchronized', async () => {
  const ui = studio(undefined, { catalog: voiceCatalog }); await ui.flush();
  ui.replies.set('/api/voices/preview', { preview_id: 'sample-toggle', audio_url: '/api/voices/preview/sample-toggle/audio' });
  await ui.preview('vieneu:Trúc Ly');
  assert.match(ui.row('vieneu:Trúc Ly').querySelector('.voice-preview-button').title, /Dừng mẫu/);
  assert.match(ui.row('vieneu:Trúc Ly').querySelector('.voice-preview-button').getAttribute('aria-label'), /Dừng mẫu giọng Trúc Ly/);
  assert.match(ui.row('vieneu:Trúc Ly').querySelector('.voice-preview-button').innerHTML, /fa-stop/);
  assert.equal(ui.row('vieneu:Trúc Ly').dataset.previewing, 'true');
  await ui.preview('vieneu:Trúc Ly');
  assert.equal(ui.el('voice-preview-audio').paused, true);
  assert.equal(ui.el('voice-preview-audio').src, undefined);
  assert.equal(ui.row('vieneu:Trúc Ly').dataset.previewing, 'false');
  assert.match(ui.row('vieneu:Trúc Ly').querySelector('.voice-preview-button').title, /Nghe thử/);
  assert.match(ui.row('vieneu:Trúc Ly').querySelector('.voice-preview-button').innerHTML, /fa-play/);
  assert.equal(ui.requests.filter(req => req.url === '/api/voices/preview').length, 1);
  assert.ok(ui.requests.some(req => req.url === '/api/voices/preview/sample-toggle' && req.options.method === 'DELETE'));
  assert.equal(ui.el('voice-select').value, voiceCatalog.default_voice_id);
});

test('cancelled preview cannot queue a second synthesis until the original reply settles', async () => {
  const ui = studio(undefined, { catalog: voiceCatalog }); await ui.flush();
  let finish;
  ui.replies.set('/api/voices/preview', () => new Promise(resolve => { finish = resolve; }));
  const pending = ui.preview('vieneu:Trúc Ly'); await ui.flush();
  await ui.choose('vieneu:Trúc Ly');
  await ui.preview('edge:vi-VN-HoaiMyNeural');
  assert.equal(ui.requests.filter(req => req.url === '/api/voices/preview').length, 1);
  assert.equal(ui.row('edge:vi-VN-HoaiMyNeural').querySelector('.voice-preview-button').disabled, true);
  finish({ ok: true, json: async () => ({ preview_id: 'cancelled', audio_url: '/api/voices/preview/cancelled/audio' }) });
  await pending;
  assert.equal(ui.row('edge:vi-VN-HoaiMyNeural').querySelector('.voice-preview-button').disabled, false);
  assert.equal(ui.el('voice-preview-audio').playCount, 0);
});

test('starting a task cancels a pending voice preview without attaching its late audio', async () => {
  const ui = studio(undefined, { catalog: voiceCatalog }); await ui.flush();
  let finish;
  ui.window.loadDroppedLocalVideo('D:/clip.mp4');
  ui.replies.set('/api/voices/preview', () => new Promise(resolve => { finish = resolve; }));
  const preview = ui.preview('vieneu:Trúc Ly'); await ui.flush();
  ui.replies.set('/api/streaming/start-local-file', { task_id: 'start-while-preview', video_url: null });
  await ui.el('btn-start').click();
  finish({ ok: true, json: async () => ({ preview_id: 'obsolete', audio_url: '/api/voices/preview/obsolete/audio' }) });
  await preview;
  assert.equal(ui.el('voice-preview-audio').playCount, 0);
  assert.ok(ui.requests.some(req => req.url === '/api/voices/preview/obsolete' && req.options.method === 'DELETE'));
});

test('voice preview reports server errors locally and rejects external audio URLs', async () => {
  for (const reply of [
    { failure: true, detail: 'Giọng này chưa sẵn sàng trên máy.' },
    { preview_id: 'external', audio_url: 'https://unrelated.example/voice.wav' },
  ]) {
    const ui = studio(undefined, { catalog: voiceCatalog }); await ui.flush();
    ui.replies.set('/api/voices/preview', reply);
    await ui.preview('vieneu:Trúc Ly');
    assert.equal(ui.el('voice-preview-status').dataset.error, 'true');
    assert.equal(ui.row('vieneu:Trúc Ly').querySelector('.voice-preview-button').disabled, false);
    assert.equal(ui.el('voice-preview-audio').playCount, 0);
    assert.equal(ui.alerts.length, 0);
  }
});

test('Douyin cookie status is local-only and import never claims the session is authenticated', async () => {
  const ui = studio({ configured: true, count: 3, message: 'server status' });
  await ui.flush();
  assert.match(ui.el('douyin-cookies-state').textContent, /Đã nhập 3 cookie/);
  assert.match(ui.el('douyin-cookies-status').textContent, /Chạy lại link Douyin/);
  assert.equal(ui.el('btn-delete-douyin-cookies').disabled, false);
  assert.match(template, /id="douyin-cookies-status" role="status" aria-live="polite"/);
  assert.ok(template.indexOf('id="douyin-cookies-panel"') < template.indexOf('id="settings-llm-provider"'));
});

test('Douyin cookie import sends a file only with fixed upload name, blocks duplicates and clears selection', async () => {
  const ui = studio(); await ui.flush();
  let finish;
  ui.replies.set('/api/douyin/cookies', () => new Promise(resolve => { finish = resolve; }));
  const input = ui.el('douyin-cookies-file');
  const file = { name: 'private-export-name.txt', size: 300 };
  input.files = [file]; input.value = 'private-export-name.txt';
  const pending = input.emit('change');
  await ui.flush();
  assert.equal(ui.el('douyin-cookies-panel')['aria-busy'], 'true');
  for (const id of ['btn-import-douyin-cookies', 'btn-refresh-douyin-cookies', 'btn-delete-douyin-cookies']) {
    assert.equal(ui.el(id).disabled, true);
  }
  await input.emit('change');
  await ui.el('btn-refresh-douyin-cookies').click();
  const posts = ui.requests.filter(req => req.url === '/api/douyin/cookies' && req.options.method === 'POST');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].options.headers['X-Studio-Request'], 'douyin-cookies');
  assert.equal(posts[0].options.body.parts[0].name, 'file');
  assert.equal(posts[0].options.body.parts[0].value, file);
  assert.equal(posts[0].options.body.parts[0].filename, 'douyin-cookies.txt');
  assert.equal(posts[0].options.headers['Content-Type'], undefined);
  finish({ ok: true, json: async () => ({ configured: true, count: 2, message: 'untrusted-file-name' }) });
  await pending;
  assert.equal(input.value, '');
  assert.equal(input.disabled, false);
  assert.equal(ui.el('douyin-cookies-panel')['aria-busy'], 'false');
  assert.match(ui.el('douyin-cookies-status').textContent, /chưa được xác minh/);
  assert.doesNotMatch(ui.el('douyin-cookies-status').textContent, /untrusted|private-export/);
  assert.equal(ui.alerts.length, 0);
});

test('Douyin cookie chooser rejects wrong extension, empty and oversized files before upload', async () => {
  for (const file of [
    { name: 'sensitive.json', size: 10 },
    { name: 'sensitive.txt', size: 0 },
    { name: 'sensitive.txt', size: 1024 * 1024 + 1 },
  ]) {
    const ui = studio({ configured: true, count: 4 }); await ui.flush();
    const input = ui.el('douyin-cookies-file');
    input.files = [file]; input.value = file.name;
    await input.emit('change');
    assert.equal(ui.requests.some(req => req.options.method === 'POST'), false);
    assert.equal(input.value, '');
    assert.match(ui.el('douyin-cookies-state').textContent, /Đã nhập 4/);
    assert.match(ui.el('douyin-cookies-status').textContent, /tối đa 1 MiB/);
    assert.doesNotMatch(ui.el('douyin-cookies-status').textContent, /sensitive/);
  }
});

test('failed Douyin import preserves existing configuration and never renders backend error contents', async () => {
  const ui = studio({ configured: true, count: 4 }); await ui.flush();
  ui.replies.set('/api/douyin/cookies', () => ({ ok: false, status: 400, json: async () => ({ detail: 'cookie-secret user-file.txt' }) }));
  const input = ui.el('douyin-cookies-file');
  input.files = [{ name: 'local.txt', size: 100 }]; input.value = 'local.txt';
  await input.emit('change');
  assert.match(ui.el('douyin-cookies-state').textContent, /Đã nhập 4/);
  assert.match(ui.el('douyin-cookies-status').textContent, /được giữ nguyên/);
  assert.doesNotMatch(ui.el('douyin-cookies-status').textContent, /cookie-secret|user-file/);
  assert.equal(ui.el('btn-delete-douyin-cookies').disabled, false);
  assert.equal(input.value, '');
});

test('late initial status cannot overwrite a completed cookie import', async () => {
  let resolveInitial;
  const ui = studio(() => new Promise(resolve => { resolveInitial = resolve; }));
  ui.replies.set('/api/douyin/cookies', { configured: true, count: 2 });
  ui.el('douyin-cookies-file').files = [{ name: 'local.txt', size: 100 }];
  await ui.el('douyin-cookies-file').emit('change');
  const success = ui.el('douyin-cookies-status').textContent;
  resolveInitial({ ok: true, json: async () => ({ configured: false, count: 0 }) });
  await ui.flush();
  assert.match(ui.el('douyin-cookies-state').textContent, /Đã nhập 2/);
  assert.equal(ui.el('douyin-cookies-status').textContent, success);
  assert.equal(ui.el('btn-delete-douyin-cookies').disabled, false);
});

test('cookie deletion uses explicit request header, retains state after network failure, and refresh recovers', async () => {
  const ui = studio({ configured: true, count: 4 }); await ui.flush();
  ui.replies.set('/api/douyin/cookies', () => { throw new Error('private-network-detail'); });
  await ui.el('btn-delete-douyin-cookies').click();
  const request = ui.requests.find(req => req.options.method === 'DELETE');
  assert.equal(request.options.headers['X-Studio-Request'], 'douyin-cookies');
  assert.equal(request.options.body, undefined);
  assert.match(ui.el('douyin-cookies-state').textContent, /Đã nhập 4/);
  assert.doesNotMatch(ui.el('douyin-cookies-status').textContent, /private-network-detail/);
  assert.equal(ui.el('btn-delete-douyin-cookies').disabled, false);
  ui.replies.set('/api/douyin/cookies', { configured: true, count: 4 });
  await ui.el('btn-refresh-douyin-cookies').click();
  assert.match(ui.el('douyin-cookies-status').textContent, /Đã có cookie/);
  ui.replies.set('/api/douyin/cookies', { configured: false, count: 0 });
  await ui.el('btn-delete-douyin-cookies').click();
  assert.match(ui.el('douyin-cookies-state').textContent, /Chưa có cookie/);
  assert.match(ui.el('douyin-cookies-status').textContent, /Đã xóa bản cookie khỏi Studio/);
  assert.equal(ui.el('btn-delete-douyin-cookies').disabled, true);
});

test('cookie status failures preserve configured state without exposing malformed responses', async () => {
  const ui = studio({ configured: true, count: 4 }); await ui.flush();
  ui.replies.set('/api/douyin/cookies', { configured: 'secret-value', count: '<unsafe>' });
  await ui.el('btn-refresh-douyin-cookies').click();
  assert.match(ui.el('douyin-cookies-state').textContent, /Đã nhập 4/);
  assert.match(ui.el('douyin-cookies-status').textContent, /Không kiểm tra được trạng thái/);
  assert.doesNotMatch(ui.el('douyin-cookies-status').textContent, /secret-value|unsafe/);
  assert.equal(ui.el('btn-refresh-douyin-cookies').disabled, false);
});

test('expired saved cookies remain removable from Settings', async () => {
  const ui = studio({ configured: false, stored: true, count: 0 }); await ui.flush();
  assert.match(ui.el('douyin-cookies-state').textContent, /hết hạn/);
  assert.equal(ui.el('btn-delete-douyin-cookies').disabled, false);
  ui.replies.set('/api/douyin/cookies', { configured: false, stored: false, count: 0 });
  await ui.el('btn-delete-douyin-cookies').click();
  assert.equal(ui.requests.filter(req => req.options.method === 'DELETE').length, 1);
  assert.equal(ui.el('btn-delete-douyin-cookies').disabled, true);
});

test('saved buffer and arbitrary configured model are displayed and sent to backend', async () => {
  const ui = studio(); await ui.start();
  assert.equal(ui.el('settings-gemini-model').tagName, 'INPUT');
  assert.equal(ui.el('settings-gemini-model').options, undefined);
  assert.equal(ui.el('settings-gemini-model').value, 'gemini-fixture');
  assert.equal(ui.el('buffer-select').value, '15');
  const request = ui.requests.find(req => req.url.includes('start-local-file'));
  assert.equal(JSON.parse(request.options.body).initial_buffer_seconds, 15);
});

test('library selection previews an existing local video and waits for an explicit start', async () => {
  const ui = studio(); await ui.flush();
  const item = { name: 'Bản gốc & bản dịch.mp4', file_path: 'D:/Studio/inputs/Bản gốc & bản dịch.mp4', size: 28510209, modified_at: 1791200000 };
  ui.replies.set('/api/library', { items: [item] });
  await ui.el('source-tab-library').click(); await ui.flush();
  assert.equal(ui.el('source-library-panel').classList.contains('hidden'), false);
  assert.equal(ui.el('source-url-panel').classList.contains('hidden'), true);
  assert.equal(ui.el('source-tab-library').getAttribute('aria-pressed'), 'true');
  assert.ok(ui.requests.some(request => request.url === '/api/library'));
  const selection = ui.el('library-list').querySelector('button');
  assert.ok(selection, 'Library must offer a selectable existing video');
  await selection.click();
  assert.equal(ui.window.currentLocalFilePath, item.file_path);
  assert.equal(ui.el('video-url').value, '');
  assert.equal(new URL(ui.el('video-player').src, 'http://localhost').searchParams.get('path'), item.file_path);
  assert.match(ui.el('file-name-display').textContent, /Bản gốc & bản dịch\.mp4/);
  assert.equal(ui.requests.some(request => request.url.includes('/streaming/start')), false);
  ui.replies.set('/api/streaming/start-local-file', { task_id: 'library-fixture', video_url: '/fixture.mp4' });
  await ui.el('btn-start').click();
  const request = ui.requests.find(request => request.url === '/api/streaming/start-local-file');
  assert.equal(JSON.parse(request.options.body).file_path, item.file_path);
  assert.equal(ui.requests.some(request => request.url === '/api/streaming/start-url'), false);
});

test('library explains empty and failed listings and switching to URL preserves the source controls', async () => {
  for (const [reply, expected] of [[{ items: [] }, /chưa|trống/i], [{ failure: true }, /chưa đọc được|không|lỗi/i]]) {
    const ui = studio(); await ui.flush();
    ui.replies.set('/api/library', reply);
    await ui.el('source-tab-library').click(); await ui.flush();
    assert.match(ui.el('library-status').textContent, expected);
    assert.equal(ui.el('library-list').querySelectorAll('button').length, 0);
    await ui.el('source-tab-url').click();
    assert.equal(ui.el('source-url-panel').classList.contains('hidden'), false);
    assert.equal(ui.el('source-library-panel').classList.contains('hidden'), true);
    assert.equal(ui.el('source-tab-url').getAttribute('aria-pressed'), 'true');
    assert.equal(ui.requests.some(request => request.url.includes('/streaming/start')), false);
  }
});

test('custom player buttons play and pause the selected preview and control its audio', async () => {
  const ui = studio(); await ui.flush();
  ui.window.loadDroppedLocalVideo('D:/preview.mp4');
  const video = ui.el('video-player');
  await ui.el('player-play-toggle').click();
  assert.equal(video.paused, false);
  assert.match(ui.el('player-play-toggle').getAttribute('aria-label'), /Tạm dừng/);
  await ui.el('player-play-toggle').click();
  assert.equal(video.paused, true);
  assert.match(ui.el('player-play-toggle').getAttribute('aria-label'), /Phát/);
  ui.el('player-volume').value = '0.4';
  await ui.el('player-volume').emit('input');
  assert.equal(video.volume, 0.4);
  await ui.el('player-mute-toggle').click();
  assert.equal(video.muted || video.volume === 0, true);
  assert.match(ui.el('player-mute-toggle').getAttribute('aria-label'), /Bật tiếng/);
  await ui.el('player-mute-toggle').click();
  assert.equal(video.muted, false);
  assert.equal(video.volume, 0.4);
  await ui.el('player-fullscreen').click();
  assert.equal(ui.playerSurface.fullscreenCount, 1);
  assert.equal(ui.document.fullscreenElement, ui.playerSurface);
  await ui.el('player-fullscreen').click();
  assert.equal(ui.document.fullscreenElement, null);
  assert.equal(ui.requests.some(request => request.url.includes('/streaming/start')), false);
});

test('paused seek never plays dubbing, play resumes same segment, and speed stays synchronized', async () => {
  const ui = studio(); await ui.start();
  const video = ui.el('video-player');
  video.currentTime = 2; await video.emit('timeupdate');
  const dub = ui.audio.find(item => item.src === '/dub.wav');
  assert.equal(dub.playCount, 0);
  await video.play(); assert.equal(dub.playCount, 1);
  video.pause(); assert.equal(dub.paused, true);
  await video.play(); assert.equal(dub.playCount, 2);
  video.playbackRate = 1.5; await video.emit('ratechange');
  assert.equal(dub.playbackRate, 1.5);
  const bgm = ui.audio.find(item => item.src === '/bgm.m4a');
  assert.equal(bgm.playbackRate, 1.5);
  assert.ok(Math.abs(bgm.volume - 0.3 * 10 ** (-18 / 20)) < 0.00001);
});

test('master mute and volume apply to translated speech and background without unmuting original speech', async () => {
  const ui = studio(); await ui.start();
  const video = ui.el('video-player');
  video.currentTime = 2;
  await ui.el('player-play-toggle').click();
  const dub = ui.audio.find(item => item.src === '/dub.wav');
  const bgm = ui.audio.find(item => item.src === '/bgm.m4a');
  assert.ok(dub && bgm);
  assert.equal(video.muted, true, 'Original speech stays muted during dubbing');
  ui.el('vol-dub').value = '0.8'; await ui.el('vol-dub').emit('input');
  ui.el('player-volume').value = '0.5'; await ui.el('player-volume').emit('input');
  assert.equal(dub.volume, 0.4);
  assert.ok(Math.abs(bgm.volume - 0.3 * 0.5 * 10 ** (-18 / 20)) < 0.00001);
  await ui.el('player-mute-toggle').click();
  assert.equal(dub.volume, 0);
  assert.equal(bgm.volume, 0);
  assert.equal(video.muted, true);
  assert.equal(ui.el('vol-dub').value, '0.8');
  assert.equal(ui.el('vol-bgm').value, '0.3');
  await ui.el('player-mute-toggle').click();
  assert.equal(dub.volume, 0.4);
  assert.ok(Math.abs(bgm.volume - 0.3 * 0.5 * 10 ** (-18 / 20)) < 0.00001);
  assert.equal(video.muted, true);
});

test('timeline uses current marker after init and replays READY colors', async () => {
  const ui = studio(); await ui.start();
  ui.el('video-player').currentTime = 5; await ui.el('video-player').emit('timeupdate');
  assert.equal(ui.el('playback-head-marker').style.left, '50%');
  assert.match(ui.el('slice-seg-0').className, /emerald/);
  assert.equal(ui.el('seg-vi-0').textContent, '<b>Xin chào</b>');
  assert.equal(ui.el('seg-vi-0').innerHTML, '');
});

test('playback waits for an untranslated segment and resumes when its audio is ready', async () => {
  const ui = studio(); await ui.start();
  const socket = ui.sockets.at(-1), video = ui.el('video-player');
  const segment = { id: 0, start: 0, end: 10, duration: 10, status: 'TTS' };
  socket.receive({ type: 'segment_update', ...segment });
  await video.play();
  assert.equal(video.paused, true);
  assert.equal(ui.el('buffering-alert').classList.contains('hidden'), false);
  socket.receive({ type: 'segment_update', ...segment, status: 'READY', audio_url: '/ready.wav' });
  await ui.flush();
  assert.equal(video.paused, false);
  assert.equal(ui.el('buffering-alert').classList.contains('hidden'), true);
});

test('worker errors do not pretend pause succeeded; success updates Studio from Tasks', async () => {
  const ui = studio(); await ui.start();
  ui.replies.set('/api/tasks/fixture/pause', { failure: true, detail: 'worker unavailable' });
  await ui.window.pauseTask('fixture');
  assert.match(ui.alerts.at(-1), /worker unavailable/);
  assert.equal(ui.el('btn-pause-worker').classList.contains('hidden'), false);
  ui.replies.set('/api/tasks/fixture/pause', { status: 'ok' });
  await ui.window.pauseTask('fixture');
  assert.equal(ui.el('btn-pause-worker').classList.contains('hidden'), true);
  assert.equal(ui.el('btn-resume-worker').classList.contains('hidden'), false);
});

test('reopening export cannot create a duplicate export', async () => {
  const ui = studio(); await ui.start();
  let finish;
  ui.replies.set('/api/streaming/export-hq', () => new Promise(resolve => { finish = resolve; }));
  const exporting = ui.el('btn-confirm-export').click();
  await ui.flush();
  await ui.el('btn-export-hq').click();
  assert.equal(ui.el('btn-confirm-export').classList.contains('hidden'), true);
  await ui.el('btn-confirm-export').click();
  assert.equal(ui.requests.filter(req => req.url === '/api/streaming/export-hq').length, 1);
  finish({ ok: true, json: async () => ({ video_url: '/out.mp4', output_filename: 'out.mp4' }) });
  await exporting;
});

test('stop clears deleted dubbing media before playback can resume', async () => {
  const ui = studio(); await ui.start();
  const video = ui.el('video-player');
  video.currentTime = 2; await video.play();
  const previousAudioCount = ui.audio.length;
  ui.replies.set('/api/tasks/fixture/stop', { status: 'ok' });
  await ui.window.studioStop();
  assert.equal(video.paused, true);
  assert.equal(ui.el('subtitle-text').textContent, '');
  await video.play(); await video.emit('timeupdate');
  assert.equal(ui.audio.length, previousAudioCount);
  assert.equal(ui.el('segments-count-badge').textContent, '0 câu');
});

async function startUrl(ui) {
  await ui.flush();
  ui.el('video-url').value = 'https://v.douyin.com/_IAiSDH0bK8/';
  await ui.el('video-url').emit('input');
  ui.replies.set('/api/streaming/start-url', { task_id: 'download-fixture', video_url: null });
  await ui.el('btn-start').click();
  await ui.flush();
  return ui.sockets.at(-1);
}

test('pasted share URL is recognized, clears a prior file and sends only the extracted URL', async () => {
  const ui = studio(); await ui.flush();
  ui.window.loadDroppedLocalVideo('D:/previous.mp4');
  ui.el('video-url').value = '复制打开抖音 https://v.douyin.com/_IAiSDH0bK8/ 看视频';
  await ui.el('video-url').emit('input');
  assert.equal(ui.window.currentLocalFilePath, null);
  assert.match(ui.el('video-url-status').textContent, /Đã nhận link Douyin/);
  ui.replies.set('/api/streaming/start-url', { task_id: 'download-fixture', video_url: null });
  await ui.el('btn-start').click();
  const request = ui.requests.find(item => item.url === '/api/streaming/start-url');
  assert.equal(JSON.parse(request.options.body).url, 'https://v.douyin.com/_IAiSDH0bK8/');
  assert.equal(ui.requests.some(item => item.url === '/api/streaming/start-local-file'), false);
});

test('paste instantly replaces a whole share message with its clean link and never starts a task', async () => {
  const target = 'https://v.douyin.com/_lAiSDH0bK8/';
  for (const pasted of [
    `3.53 复制打开抖音，看看【斩漫的作品】《千金垂爱》 # ai动漫 ${target} Rxf:/ 01/17 J@V.Lw :1pm`,
    String.raw`3.53 复制打开抖音 [**https://v.douyin.com/\_lAiSDH0bK8/**](https://v.douyin.com/_lAiSDH0bK8/) Rxf:/ 01/17 J@V.Lw :1pm`,
  ]) {
    const ui = studio(); await ui.flush();
    ui.window.loadDroppedLocalVideo('D:/previous.mp4');
    let prevented = false;
    await ui.el('video-url').emit('paste', {
      clipboardData: { getData: type => type === 'text/plain' ? pasted : '' },
      preventDefault() { prevented = true; },
    });
    assert.equal(prevented, true);
    assert.equal(ui.el('video-url').value, target);
    assert.equal(ui.window.currentLocalFilePath, null);
    assert.equal(ui.el('btn-start').disabled, false);
    assert.equal(ui.el('video-url')['aria-invalid'], 'false');
    assert.ok(ui.el('video-url-status').textContent.includes(target));
    assert.equal(ui.requests.some(req => req.url.includes('/streaming/start')), false);
    ui.window.loadDroppedLocalVideo('D:/replacement.mp4');
    assert.equal(ui.el('video-url').value, '');
    assert.equal(ui.window.currentLocalFilePath, 'D:/replacement.mp4');
  }
});

test('non-link paste keeps native text editing and never discards the existing source', async () => {
  const ui = studio(); await ui.flush();
  ui.window.loadDroppedLocalVideo('D:/previous.mp4');
  let prevented = false;
  await ui.el('video-url').emit('paste', {
    clipboardData: { getData: () => 'just a title' }, preventDefault() { prevented = true; },
  });
  assert.equal(prevented, false);
  assert.equal(ui.window.currentLocalFilePath, 'D:/previous.mp4');
  assert.equal(ui.el('video-url').value, '');
});

test('whole Douyin share text including copied Markdown sends exactly its video link', async () => {
  const target = 'https://v.douyin.com/_lAiSDH0bK8/';
  const samples = [
    `3.53 复制打开抖音，看看【斩漫的作品】《千金垂爱》被千金拉着结婚了 # ai动漫 # a... ${target} Rxf:/ 01/17 J@V.Lw :1pm`,
    String.raw`3.53 复制打开抖音，看看【斩漫的作品】《千金垂爱》被千金拉着结婚了 # ai动漫 # a... [**https://v.douyin.com/\_lAiSDH0bK8/**](https://v.douyin.com/_lAiSDH0bK8/) Rxf:/ 01/17 J@V.Lw :1pm`,
    `复制打开抖音\n${target}\nRxf:/ 01/17 J@V.Lw :1pm`,
    `复制[https://example.com/wrong](${target})后打开`,
    `**${target}**`, `\`${target}\``, `__${target}__`,
    String.raw`https://v.douyin.com/\_lAiSDH0bK8/`,
  ];
  for (const input of samples) {
    const ui = studio(); await ui.flush();
    assert.equal(ui.el('video-url').tagName, 'TEXTAREA');
    ui.el('video-url').value = input;
    await ui.el('video-url').emit('input');
    assert.equal(ui.el('btn-start').disabled, false, input);
    assert.equal(ui.el('video-url').value, input, 'Keep original share text editable');
    assert.match(ui.el('video-url-status').textContent, /Đã nhận link Douyin: https:\/\/v\.douyin\.com\/_lAiSDH0bK8\//);
    assert.equal(ui.requests.some(req => req.url.includes('/streaming/start')), false, 'Pasting must not start a download');
    ui.replies.set('/api/streaming/start-url', { task_id: 'share-text-fixture', video_url: null });
    await ui.el('btn-start').click();
    const request = ui.requests.find(req => req.url === '/api/streaming/start-url');
    assert.equal(JSON.parse(request.options.body).url, target, input);
  }
});

test('share text preserves signed URL query and rejects credentials in Markdown target', async () => {
  const target = 'https://example.com/video.mp4?token=a%2Fb%2Bz&part=*&nested=**https://nested/**';
  const ui = studio(); await ui.flush();
  ui.el('video-url').value = `Watch ${target}`;
  await ui.el('video-url').emit('input');
  assert.doesNotMatch(ui.el('video-url-status').textContent, /token=|nested=/);
  ui.replies.set('/api/streaming/start-url', { task_id: 'query-fixture', video_url: null });
  await ui.el('btn-start').click();
  assert.equal(JSON.parse(ui.requests.find(req => req.url === '/api/streaming/start-url').options.body).url, target);
  const invalid = studio(); await invalid.flush();
  invalid.el('video-url').value = '[https://v.douyin.com/valid/](https://user:secret@v.douyin.com/private/)';
  await invalid.el('video-url').emit('input');
  assert.equal(invalid.el('btn-start').disabled, true);
  assert.doesNotMatch(invalid.el('video-url-status').textContent, /secret/);
});

test('invalid URL is explained before any download request', async () => {
  const ui = studio(); await ui.flush();
  ui.el('video-url').value = 'not a video link';
  await ui.el('video-url').emit('input');
  await ui.el('btn-start').click();
  assert.equal(ui.el('btn-start').disabled, true);
  assert.equal(ui.el('video-url')['aria-invalid'], 'true');
  assert.match(ui.el('video-url-status').textContent, /Chưa nhận được link hợp lệ/);
  assert.equal(ui.requests.some(item => item.url.includes('/streaming/start')), false);
});

test('URL normalization matches supported bare domains, share punctuation and strips fragments', async () => {
  for (const [input, expected] of [
    ['v.douyin.com/example/', 'https://v.douyin.com/example/'],
    ['www.youtube.com/watch?v=example', 'https://www.youtube.com/watch?v=example'],
    ['youtu.be/example#play', 'https://youtu.be/example'],
    ['复制（https://v.douyin.com/example/），打开抖音', 'https://v.douyin.com/example/'],
    ['Watch https://example.com/video.mp4). next', 'https://example.com/video.mp4'],
    ['Watch https://example.com/video.mp4» next', 'https://example.com/video.mp4'],
  ]) {
    const ui = studio(); await ui.flush();
    ui.el('video-url').value = input;
    await ui.el('video-url').emit('input');
    assert.equal(ui.el('btn-start').disabled, false, input);
    ui.replies.set('/api/streaming/start-url', { task_id: 'url-normalized', video_url: null });
    await ui.el('btn-start').click();
    const request = ui.requests.find(item => item.url === '/api/streaming/start-url');
    assert.equal(JSON.parse(request.options.body).url, expected, input);
  }
});

test('URL validation rejects credentials, invalid ports and unsupported bare domains', async () => {
  for (const input of ['https://user:secret@v.douyin.com/example/', 'https://user@v.douyin.com/example/', 'https://v.douyin.com:99999/example/', 'example.com/video.mp4']) {
    const ui = studio(); await ui.flush();
    ui.el('video-url').value = input;
    await ui.el('video-url').emit('input');
    await ui.el('btn-start').click();
    assert.equal(ui.el('btn-start').disabled, true, input);
    assert.equal(ui.requests.some(item => item.url.includes('/streaming/start')), false, input);
  }
});

test('pending URL task displays honest progress, controls and source-ready preview', async () => {
  const ui = studio(), socket = await startUrl(ui);
  assert.equal(ui.el('task-progress').classList.contains('hidden'), false);
  assert.equal(ui.el('btn-pause-worker').classList.contains('hidden'), true);
  assert.equal(ui.el('btn-stop-worker').classList.contains('hidden'), false);
  assert.equal(ui.el('btn-export-hq').disabled, true);
  assert.equal(ui.el('video-player').src, undefined);
  socket.receive({ type: 'progress', phase: 'download', stage: 'Đang tải video', progress_pct: 42.4, status: 'RUNNING', can_pause: false });
  assert.equal(ui.el('task-progress-value').textContent, '42.4%');
  assert.equal(ui.el('task-progress-bar').style.width, '42.4%');
  assert.equal(ui.el('task-progress-track')['aria-valuenow'], '42.4');
  socket.receive({ type: 'progress', phase: 'prepare', stage: 'Đang tách âm thanh', progress_pct: null, status: 'RUNNING', can_pause: false });
  assert.equal(ui.el('task-progress-value').textContent, 'Chưa có %');
  assert.equal(ui.el('task-progress-track')['aria-valuenow'], undefined);
  assert.equal(ui.el('buffering-text').textContent, 'Đang tách âm thanh');
  socket.receive({ type: 'source_ready', video_url: '/api/video/download-fixture' });
  assert.equal(ui.el('video-player').src, '/api/video/download-fixture');
  assert.equal(ui.el('btn-export-hq').disabled, true);
});

test('player shows measured download percentage, bytes, speed and ETA beside the loading state', async () => {
  const ui = studio(), socket = await startUrl(ui);
  const downloading = {
    type: 'progress', phase: 'download', stage: 'Đang tải bản gốc Douyin 1080p',
    progress_pct: 2.2, status: 'RUNNING', can_pause: false,
    downloaded_bytes: 438680000, total_bytes: 19940000000, speed: 12500000, eta: 120,
  };
  socket.receive(downloading);
  assert.equal(ui.el('player-download-progress').classList.contains('hidden'), false);
  assert.equal(ui.el('player-progress-value').textContent, '2.2%');
  assert.equal(ui.el('task-progress-value').textContent, '2.2%');
  assert.equal(ui.el('player-progress-bar').style.width, '2.2%');
  const firstDetail = ui.el('player-progress-detail').textContent;
  assert.match(firstDetail, /(?:438[.,]68\s*MB|0[.,]44\s*GB)/);
  assert.match(firstDetail, /19[.,]94\s*GB/);
  assert.match(firstDetail, /12[.,]5(?:0)?\s*MB\/s/);
  assert.match(firstDetail, /(?:2\s*(?:phút|min)|0?2:00)/);

  socket.receive({ ...downloading, downloaded_bytes: 2200000000, progress_pct: 11 });
  assert.match(ui.el('player-progress-detail').textContent, /2[.,]2(?:0)?\s*GB/);
  assert.match(ui.el('player-progress-detail').textContent, /19[.,]94\s*GB/);
  assert.equal(ui.el('player-progress-bar').style.width, '11%');

  socket.receive({ type: 'progress', phase: 'prepare', stage: 'Đang kiểm tra tệp video', progress_pct: null, status: 'RUNNING' });
  assert.equal(ui.el('player-download-progress').classList.contains('hidden'), true);
  assert.equal(ui.el('buffering-text').textContent, 'Đang kiểm tra tệp video');
  assert.equal(ui.requests.some(request => request.url.endsWith('/stop')), false);
});

test('unknown download total never invents a percentage while keeping actual received bytes visible', async () => {
  const ui = studio(), socket = await startUrl(ui);
  socket.receive({
    type: 'progress', phase: 'download', stage: 'Đang tải video', progress_pct: null,
    status: 'RUNNING', downloaded_bytes: 1250000000, total_bytes: null, speed: null, eta: null,
  });
  assert.equal(ui.el('player-download-progress').classList.contains('hidden'), false);
  assert.doesNotMatch(ui.el('player-progress-value').textContent, /\d[.,]?\d*%/);
  assert.match(ui.el('player-progress-detail').textContent, /1[.,]25\s*GB/);
  assert.doesNotMatch(ui.el('player-progress-detail').textContent, /NaN|Infinity|null|undefined/);
});

test('Studio polls with an open websocket and reconciles download progress then an external stop', async () => {
  const ui = studio(), socket = await startUrl(ui);
  ui.el('view-tasks').classList.add('hidden');
  socket.receive({ type: 'progress', phase: 'download', stage: 'Đang tải video', progress_pct: 1, status: 'RUNNING' });
  const downloads = {
    task_id: 'download-fixture', task_type: 'Dịch video', phase: 'download', status: 'RUNNING',
    stage: 'Đang tải bản gốc Douyin 1080p', progress_pct: 2.2,
    downloaded_bytes: 438680000, total_bytes: 19940000000, speed: 12500000, eta: 120,
  };
  ui.replies.set('/api/tasks', { tasks: [downloads] });
  const requestCount = ui.requests.filter(request => request.url === '/api/tasks').length;
  assert.equal(socket.readyState, 1);
  await ui.tickIntervals(2000);
  assert.ok(ui.requests.filter(request => request.url === '/api/tasks').length > requestCount);
  assert.equal(ui.el('player-progress-value').textContent, '2.2%');
  assert.equal(ui.el('task-progress-stage').textContent, downloads.stage);
  assert.equal(ui.el('buffering-alert').classList.contains('hidden'), false);

  ui.replies.set('/api/tasks', { tasks: [{ ...downloads, status: 'STOPPED', phase: 'stopped', stage: 'Đã dừng tác vụ' }] });
  await ui.tickIntervals(2000);
  assert.equal(ui.el('buffering-alert').classList.contains('hidden'), true);
  assert.equal(ui.el('player-download-progress').classList.contains('hidden'), true);
  assert.equal(ui.el('player-task-status').classList.contains('hidden'), false);
  assert.match(ui.el('player-task-status').textContent, /Đã dừng/);
  assert.equal(ui.el('btn-start').classList.contains('hidden'), false);
  assert.equal(ui.el('btn-stop-worker').classList.contains('hidden'), true);
  assert.equal(ui.requests.some(request => request.url.endsWith('/stop')), false);
});

test('failed task polling warns in the player despite an apparently open websocket and recovers', async () => {
  for (const failure of [
    { failure: true, detail: 'service unavailable' },
    () => { throw new Error('network offline'); },
  ]) {
    const ui = studio(), socket = await startUrl(ui);
    ui.el('view-tasks').classList.add('hidden');
    socket.receive({ type: 'progress', phase: 'download', stage: 'Đang tải video', progress_pct: 2.2, status: 'RUNNING' });
    ui.replies.set('/api/tasks', failure);
    await ui.tickIntervals(2000);
    assert.equal(socket.readyState, 1);
    assert.equal(ui.el('player-task-status').classList.contains('hidden'), false);
    assert.match(ui.el('player-task-status').textContent, /Không kết nối/);
    assert.equal(ui.el('player-task-status').dataset.state, 'disconnected');
    assert.equal(ui.el('player-progress-value').textContent, '2.2%');
    assert.equal(ui.requests.some(request => request.url.endsWith('/stop')), false);

    ui.replies.set('/api/tasks', { tasks: [{ task_id: 'download-fixture', phase: 'download', stage: 'Đang tải video', progress_pct: 3.4, status: 'RUNNING' }] });
    await ui.tickIntervals(2000);
    assert.equal(ui.el('player-progress-value').textContent, '3.4%');
    assert.notEqual(ui.el('player-task-status').dataset.state, 'disconnected');
  }
});

test('missing active task is reported beside the player without pretending the download completed', async () => {
  const ui = studio(), socket = await startUrl(ui);
  ui.el('view-tasks').classList.add('hidden');
  socket.receive({ type: 'progress', phase: 'download', stage: 'Đang tải video', progress_pct: 2.2, status: 'RUNNING' });
  ui.replies.set('/api/tasks', { tasks: [] });
  await ui.tickIntervals(2000);
  assert.equal(ui.el('player-task-status').classList.contains('hidden'), false);
  assert.match(ui.el('player-task-status').textContent, /Không tìm thấy/);
  assert.notEqual(ui.el('task-progress').dataset.status, 'COMPLETED');
  assert.equal(ui.el('btn-export-hq').disabled, true);
  assert.equal(ui.requests.some(request => request.url.endsWith('/stop')), false);
});

test('an identical valid task snapshot clears connection and missing-task warnings', async () => {
  for (const unavailableReply of [{ failure: true }, { tasks: [] }]) {
    const ui = studio(), socket = await startUrl(ui);
    await ui.flush();
    const progress = { task_id: 'download-fixture', phase: 'download', stage: 'Đang tải video',
      progress_pct: 2.2, status: 'RUNNING', downloaded_bytes: 438680000,
      total_bytes: 19940000000, speed: 12500000, eta: 120 };
    socket.receive({ type: 'progress', ...progress });
    ui.replies.set('/api/tasks', unavailableReply);
    await ui.el('btn-refresh-tasks').click();
    assert.equal(ui.el('player-task-status').dataset.state, 'disconnected');
    assert.equal(ui.el('player-task-status').classList.contains('hidden'), false);

    ui.replies.set('/api/tasks', { tasks: [progress] });
    await ui.el('btn-refresh-tasks').click();
    assert.equal(ui.el('player-task-status').classList.contains('hidden'), true);
    assert.notEqual(ui.el('player-task-status').dataset.state, 'disconnected');
    assert.equal(ui.el('task-connection-status').classList.contains('hidden'), true);
    assert.equal(ui.el('player-download-progress').classList.contains('hidden'), false);
    assert.equal(ui.el('player-progress-value').textContent, '2.2%');
    assert.equal(ui.el('buffering-alert').classList.contains('hidden'), false);
    assert.equal(ui.requests.some(request => request.url.endsWith('/stop')), false);
  }
});

test('a slow task snapshot cannot overwrite progress or introduce a warning after a newer websocket event', async () => {
  for (const staleReply of [
    { tasks: [{ task_id: 'download-fixture', phase: 'download', status: 'RUNNING', stage: 'Đang tải video', progress_pct: 1 }] },
    { tasks: [] },
  ]) {
    const ui = studio(), socket = await startUrl(ui);
    await ui.flush();
    socket.receive({ type: 'progress', phase: 'download', status: 'RUNNING', stage: 'Đang tải video', progress_pct: 1 });
    let finishSnapshot;
    ui.replies.set('/api/tasks', () => new Promise(resolve => { finishSnapshot = resolve; }));
    const refreshing = ui.el('btn-refresh-tasks').click();
    await ui.flush();
    socket.receive({ type: 'progress', phase: 'download', status: 'RUNNING', stage: 'Đang tải bản gốc Douyin', progress_pct: 3.4 });
    finishSnapshot({ ok: true, json: async () => staleReply });
    await refreshing;
    assert.equal(ui.el('player-progress-value').textContent, '3.4%');
    assert.equal(ui.el('task-progress-stage').textContent, 'Đang tải bản gốc Douyin');
    assert.equal(ui.el('player-download-progress').classList.contains('hidden'), false);
    assert.equal(ui.el('player-task-status').classList.contains('hidden'), true);
  }
});

test('slow task polling allows only one request in flight and resumes after it settles', async () => {
  const ui = studio(), socket = await startUrl(ui);
  socket.receive({ type: 'progress', phase: 'download', status: 'RUNNING', stage: 'Đang tải video', progress_pct: 2.2 });
  let finishSnapshot;
  ui.replies.set('/api/tasks', () => new Promise(resolve => { finishSnapshot = resolve; }));
  const before = ui.requests.filter(request => request.url === '/api/tasks').length;
  const refreshing = ui.el('btn-refresh-tasks').click();
  await ui.flush();
  await ui.tickIntervals(2000);
  await ui.el('btn-refresh-tasks').click();
  await ui.tickIntervals(2000);
  assert.equal(ui.requests.filter(request => request.url === '/api/tasks').length, before + 1);
  const snapshot = { tasks: [{ task_id: 'download-fixture', phase: 'download', status: 'RUNNING', stage: 'Đang tải video', progress_pct: 2.4 }] };
  finishSnapshot({ ok: true, json: async () => snapshot });
  await refreshing;
  ui.replies.set('/api/tasks', snapshot);
  await ui.tickIntervals(2000);
  assert.equal(ui.requests.filter(request => request.url === '/api/tasks').length, before + 2);
  assert.equal(ui.el('player-progress-value').textContent, '2.4%');
});

test('stopping before task-id arrives cancels the returned task and never attaches its media', async () => {
  const ui = studio(); await ui.flush();
  let finish;
  ui.el('video-url').value = 'https://v.douyin.com/example/';
  ui.replies.set('/api/streaming/start-url', () => new Promise(resolve => { finish = resolve; }));
  const starting = ui.el('btn-start').click();
  await ui.flush();
  await ui.window.studioStop();
  assert.equal(ui.el('task-progress').dataset.status, 'CANCELLING');
  assert.equal(ui.el('btn-stop-worker').disabled, true);
  finish({ ok: true, json: async () => ({ task_id: 'late-task', video_url: '/late.mp4' }) });
  await starting;
  assert.equal(ui.requests.filter(item => item.url === '/api/tasks/late-task/stop').length, 1);
  assert.equal(ui.sockets.length, 0);
  assert.equal(ui.el('task-progress').dataset.status, 'STOPPED');
  assert.equal(ui.el('btn-start').classList.contains('hidden'), false);
});

test('download failure remains visible without a popup or false completion', async () => {
  const ui = studio(), socket = await startUrl(ui);
  socket.receive({ type: 'error', message: 'Không tải được video: kết nối hết thời gian chờ.' });
  socket.receive({ type: 'finished', status: 'failed' });
  assert.equal(ui.el('task-progress').dataset.status, 'FAILED');
  assert.match(ui.el('task-progress-stage').textContent, /kết nối hết thời gian chờ/);
  assert.equal(ui.el('btn-export-hq').disabled, true);
  assert.equal(ui.el('btn-start').classList.contains('hidden'), false);
  assert.equal(ui.alerts.length, 0);
});

test('Tasks shows download phase without null percent; disconnected realtime shows real status', async () => {
  const ui = studio(), socket = await startUrl(ui);
  ui.replies.set('/api/tasks', { tasks: [{ task_id: 'download-fixture', task_type: 'Dịch video', status: 'RUNNING', phase: 'download', stage: 'Đang tải video', progress_pct: null, can_stop: true }] });
  await ui.el('btn-refresh-tasks').click();
  const row = ui.el('tasks-table-body').children[0];
  assert.match(row.innerHTML, /Đang tải video/);
  assert.match(row.innerHTML, /Chưa có số liệu %/);
  assert.doesNotMatch(row.innerHTML, /null%/);
  socket.onclose(); await ui.flush();
  assert.equal(ui.el('task-connection-status').classList.contains('hidden'), false);
  assert.match(ui.el('task-connection-status').textContent, /gián đoạn/);
  assert.equal(ui.el('task-progress-stage').textContent, 'Đang tải video');
});

test('terminal Tasks with no measured progress show their stage without a progress bar', async () => {
  const ui = studio(); await ui.flush();
  for (const status of ['STOPPED', 'CANCELLED', 'FAILED', 'COMPLETED']) {
    ui.replies.set('/api/tasks', { tasks: [{ task_id: 'terminal-fixture', task_type: 'Dịch video', status, stage: 'Kết thúc tác vụ', progress_pct: null }] });
    await ui.el('btn-refresh-tasks').click();
    const row = ui.el('tasks-table-body').children[0].innerHTML;
    assert.match(row, /Kết thúc tác vụ/, status);
    assert.doesNotMatch(row, /Chưa có số liệu|bg-gradient-to-r|null%/, status);
  }
});

test('Diagnostics preserves log lines, safely highlights levels and copies exact raw text', async () => {
  const ui = studio(); await ui.flush();
  const raw = '2026-10-05 INFO first line\n2026-10-05 ERROR <script>unsafe</script>\n    traceback detail\n';
  ui.replies.set('/api/diagnostics/logs?category=app&lines=150', { logs: raw });
  await ui.el('btn-refresh-log').click();
  assert.match(ui.el('log-console-output').innerHTML, /<\/span>\n<span/);
  assert.match(ui.el('log-console-output').innerHTML, /log-line-error/);
  assert.match(ui.el('log-console-output').innerHTML, /&lt;script&gt;/);
  assert.match(ui.el('log-summary').textContent, /1 lỗi/);
  await ui.el('btn-copy-log').click();
  assert.equal(ui.copied[0], raw);
  assert.match(ui.el('log-copy-status').textContent, /Đã sao chép/);
  assert.equal(ui.alerts.length, 0);
});

test('Diagnostics uses native clipboard in desktop and reports a copying failure visibly', async () => {
  const ui = studio(); await ui.flush();
  ui.replies.set('/api/diagnostics/logs?category=app&lines=150', { logs: 'native\nlog' });
  await ui.el('btn-refresh-log').click();
  let nativeText;
  ui.window.desktopBridge = { copyText(text, callback) { nativeText = text; callback(true); } };
  await ui.el('btn-copy-log').click();
  assert.equal(nativeText, 'native\nlog');
  assert.equal(ui.copied.length, 0);
  ui.window.desktopBridge.copyText = () => { throw new Error('clipboard denied'); };
  await ui.el('btn-copy-log').click();
  assert.match(ui.el('log-copy-status').textContent, /Không truy cập được clipboard/);
});

test('transcript timestamp seeks its sentence, highlights playback and text opens an editable draft', async () => {
  const ui = studio(); await ui.start();
  const socket = ui.sockets.at(-1);
  socket.receive({ type: 'segment_update', id: 1, start: 5, end: 10, duration: 5, status: 'READY', final_vi: 'Một câu có thể sửa.', text_zh: '原文' });
  await ui.el('seg-row-1').querySelector('.transcript-time').click();
  assert.equal(ui.el('video-player').currentTime, 5);
  await ui.el('seg-vi-1').click();
  assert.equal(ui.el('video-player').paused, true);
  assert.equal(ui.el('seg-input-1').value, 'Một câu có thể sửa.');
  assert.equal(ui.el('seg-row-1').querySelector('.transcript-editor').hidden, false);
  assert.equal(ui.el('seg-zh-1').textContent, '原文');
});

test('transcript draft survives worker updates and same-session init, then cancel restores committed text', async () => {
  const ui = studio(); await ui.start();
  const socket = ui.sockets.at(-1);
  await ui.el('seg-vi-0').click();
  ui.el('seg-input-0').value = 'Bản người dùng đang sửa';
  await ui.el('seg-input-0').emit('input');
  assert.equal(ui.el('btn-export-hq').disabled, true);
  const ready = { id: 0, start: 0, end: 10, duration: 10, status: 'PLAYED', audio_url: '/dub.wav', final_vi: '<b>Xin chào</b>' };
  socket.receive({ type: 'segment_update', ...ready });
  socket.receive({ type: 'init', duration: 10, segments_count: 1, segments: [ready] });
  assert.equal(ui.el('seg-input-0').value, 'Bản người dùng đang sửa');
  assert.equal(ui.el('seg-row-0').querySelector('.transcript-editor').hidden, false);
  await ui.el('seg-row-0').querySelector('.transcript-editor-actions').children[1].click();
  assert.equal(ui.el('seg-vi-0').textContent, '<b>Xin chào</b>');
  assert.equal(ui.el('btn-export-hq').disabled, false);
});

test('saving transcript commits server text and a new audio revision, blocks duplicate saves and ignores stale updates', async () => {
  const ui = studio(); await ui.start();
  const video = ui.el('video-player');
  await video.play(); await video.emit('timeupdate');
  const oldDub = ui.audio.find(a => a.src === '/dub.wav');
  await ui.el('seg-vi-0').click();
  const input = ui.el('seg-input-0'); input.value = 'Bản dịch đã chỉnh.'; await input.emit('input');
  let resolve;
  ui.replies.set('/api/streaming/fixture/segments/0', () => new Promise(done => { resolve = done; }));
  const save = ui.el('seg-row-0').querySelector('.transcript-editor-actions').children[0];
  const saving = save.click(); await ui.flush(); await save.click();
  assert.equal(save.disabled, true);
  assert.equal(input.disabled, true);
  assert.equal(ui.requests.filter(r => r.options.method === 'PATCH').length, 1);
  assert.equal(ui.el('seg-vi-0').textContent, '<b>Xin chào</b>');
  const segment = { id: 0, start: 0, end: 10, duration: 10, status: 'READY', final_vi: 'Bản dịch đã chỉnh.', audio_url: '/dub.wav?rev=1', revision: 1 };
  resolve({ ok: true, json: async () => ({ segment }) }); await saving;
  assert.deepEqual(JSON.parse(ui.requests.find(r => r.options.method === 'PATCH').options.body), { final_vi: 'Bản dịch đã chỉnh.' });
  assert.equal(ui.el('seg-vi-0').textContent, 'Bản dịch đã chỉnh.');
  assert.equal(ui.el('subtitle-text').textContent, 'Bản dịch đã chỉnh.');
  assert.equal(oldDub.paused, true);
  assert.ok(ui.audio.find(a => a.src === '/dub.wav?rev=1'));
  assert.equal(ui.el('seg-row-0').querySelector('.transcript-editor').hidden, true);
  assert.equal(ui.el('btn-export-hq').disabled, false);
  ui.sockets.at(-1).receive({ type: 'segment_update', ...segment, revision: 0, final_vi: 'stale', audio_url: '/dub.wav' });
  assert.equal(ui.el('seg-vi-0').textContent, 'Bản dịch đã chỉnh.');
});

test('failed transcript save retains the draft and exposes an actionable server error; blank input never submits', async () => {
  const ui = studio(); await ui.start();
  await ui.el('seg-vi-0').click();
  const input = ui.el('seg-input-0'), actions = ui.el('seg-row-0').querySelector('.transcript-editor-actions');
  input.value = '   '; await input.emit('input'); await actions.children[0].click();
  assert.equal(ui.requests.filter(r => r.options.method === 'PATCH').length, 0);
  input.value = 'Giữ lại bản sửa'; await input.emit('input');
  ui.replies.set('/api/streaming/fixture/segments/0', { failure: true, detail: 'Đang xuất video. Hãy chờ xuất xong rồi lưu lại.' });
  await actions.children[0].click();
  assert.equal(input.value, 'Giữ lại bản sửa');
  assert.equal(input.disabled, false);
  assert.equal(ui.el('seg-row-0').querySelector('.transcript-editor').hidden, false);
  assert.match(ui.el('seg-row-0').querySelector('.transcript-edit-message').textContent, /Đang xuất video/);
  assert.equal(ui.el('seg-vi-0').textContent, '<b>Xin chào</b>');
  assert.equal(ui.el('btn-export-hq').disabled, true);
});

test('new source clears transcript drafts and source metadata preserves native aspect ratio', async () => {
  const ui = studio(); await ui.start();
  await ui.el('seg-vi-0').click();
  ui.el('seg-input-0').value = 'Một bản sửa cũ'; await ui.el('seg-input-0').emit('input');
  ui.sockets.at(-1).receive({ type: 'finished', status: 'completed' });
  ui.window.loadDroppedLocalVideo('D:/another.mp4');
  assert.match(ui.el('segments-list').innerHTML, /Câu thoại sẽ xuất hiện/);
  const video = ui.el('video-player'); video.videoWidth = 1920; video.videoHeight = 1080;
  await video.emit('loadedmetadata');
  assert.equal(ui.el('player-container').style.aspectRatio, '1920 / 1080');
});

test('source pixels remain untouched while the hidden legacy mask control stays disabled', async () => {
  const ui = studio(); await ui.flush();
  const video = ui.el('video-player');
  video.src = '/source-original.mp4';
  video.videoWidth = 1920; video.videoHeight = 1080;
  await video.emit('loadedmetadata');
  assert.equal(video.src, '/source-original.mp4');
  assert.equal(ui.el('toggle-screen-text').checked, true);
  assert.equal(ui.el('toggle-mask-chinese').checked, false);
  assert.equal(ui.el('toggle-mask-chinese').disabled, true);
  assert.ok(!ui.el('chinese-sub-mask').style.display || ui.el('chinese-sub-mask').style.display === 'none');
});

test('master mute persists through start and selecting the next source after stop', async () => {
  const ui = studio(); await ui.flush();
  await ui.el('player-mute-toggle').click();
  await ui.start();
  const video = ui.el('video-player');
  assert.equal(video.volume, 0);
  await ui.window.studioStop();
  ui.window.loadDroppedLocalVideo('D:/next.mp4');
  assert.equal(video.volume, 0);
  await ui.el('player-mute-toggle').click();
  assert.equal(video.volume, 1);
});

test('a stalled task read times out and allows polling to recover without stopping the download', async () => {
  const ui = studio(), socket = await startUrl(ui);
  socket.receive({ type: 'progress', phase: 'download', status: 'RUNNING', progress_pct: 2.2 });
  let signal;
  ui.replies.set('/api/tasks', options => new Promise((resolve, reject) => {
    signal = options.signal;
    signal.addEventListener('abort', () => reject(new Error('read timed out')));
  }));
  const poll = ui.el('btn-refresh-tasks').click();
  await ui.flush();
  await ui.tickTimeouts(8000);
  await poll;
  assert.equal(signal.aborted, true);
  assert.equal(ui.el('player-task-status').dataset.state, 'disconnected');
  ui.replies.set('/api/tasks', { tasks: [{ task_id: 'download-fixture', phase: 'download', status: 'RUNNING', progress_pct: 3.4 }] });
  await ui.el('btn-refresh-tasks').click();
  assert.equal(ui.el('player-progress-value').textContent, '3.4%');
  assert.equal(ui.el('player-task-status').classList.contains('hidden'), true);
  assert.equal(ui.requests.some(request => request.url.endsWith('/stop')), false);
});

test('server stop clears predownload spinner, controls and stale events without requiring the local Stop button', async () => {
  for (const event of [
    { type: 'progress', status: 'STOPPED', phase: 'stopped', stage: 'Đã dừng tác vụ', progress_pct: null },
    { type: 'finished', status: 'cancelled' },
    { type: 'finished', status: 'STOPPED' },
  ]) {
    const ui = studio(), socket = await startUrl(ui);
    socket.receive({ type: 'progress', status: 'RUNNING', phase: 'download', stage: 'Đang tải bản gốc Douyin 1080p', progress_pct: 12 });
    assert.equal(ui.el('buffering-alert').classList.contains('hidden'), false);
    socket.receive(event);
    assert.equal(ui.el('buffering-alert').classList.contains('hidden'), true);
    assert.equal(ui.el('buffering-alert').dataset.state, 'stopped');
    assert.equal(ui.el('buffering-text').textContent, '');
    assert.equal(ui.el('btn-start').classList.contains('hidden'), false);
    assert.equal(ui.el('btn-stop-worker').classList.contains('hidden'), true);
    assert.equal(ui.el('task-connection-status').classList.contains('hidden'), true);
    assert.equal(ui.el('btn-export-hq').disabled, true);
    assert.equal(socket.readyState, 3);
    socket.receive({ type: 'ready_to_play' });
    socket.receive({ type: 'source_ready', video_url: '/cancelled-source.mp4' });
    assert.equal(ui.el('video-player').src, undefined);
    assert.equal(ui.el('video-player').playCount, 0);
    assert.equal(ui.el('task-progress').dataset.status, 'STOPPED');
  }
});

test('Tasks terminal snapshot cleans an externally stopped session even while websocket still appears connected', async () => {
  const ui = studio(); await ui.start();
  const socket = ui.sockets.at(-1);
  socket.receive({ type: 'segment_update', id: 1, start: 5, end: 10, status: 'TTS' });
  await ui.el('video-player').play();
  ui.el('buffering-alert').classList.remove('hidden');
  ui.replies.set('/api/tasks', { tasks: [{ task_id: 'fixture', status: 'STOPPED', phase: 'stopped', stage: 'Đã dừng tác vụ', progress_pct: null }] });
  await ui.el('btn-refresh-tasks').click();
  assert.equal(ui.el('buffering-alert').classList.contains('hidden'), true);
  assert.equal(ui.el('worker-tts-badge').textContent, 'TTS: Idle');
  assert.equal(ui.el('worker-tts-badge').classList.contains('animate-pulse'), false);
  assert.equal(ui.el('video-player').paused, true);
  assert.equal(ui.el('segments-count-badge').textContent, '0 câu');
  assert.equal(ui.el('subtitle-text').textContent, '');
  assert.equal(ui.el('bar-buffer-info').textContent, 'Buffer: 0.0s');
});

test('finished completion hides old loading overlay and clears worker activity; failures remain visible', async () => {
  const ui = studio(); await ui.start();
  const socket = ui.sockets.at(-1);
  socket.receive({ type: 'segment_update', id: 0, start: 0, end: 10, status: 'TTS', final_vi: 'Xin chào' });
  ui.el('buffering-alert').classList.remove('hidden');
  socket.receive({ type: 'finished', status: 'completed' });
  assert.equal(ui.el('buffering-alert').classList.contains('hidden'), true);
  assert.equal(ui.el('worker-tts-badge').textContent, 'TTS: Idle');
  socket.receive({ type: 'finished', status: 'failed', message: 'Dịch vụ không phản hồi' });
  assert.equal(ui.el('buffering-alert').classList.contains('hidden'), false);
  assert.equal(ui.el('buffering-alert').dataset.state, 'error');
  assert.equal(ui.el('buffering-text').textContent, 'Dịch vụ không phản hồi');
});

test('visual translation choice is sent explicitly on every start path and stays fixed during the task', async () => {
  for (const enabled of [false, true]) {
    for (const path of ['url', 'local-file', 'upload']) {
      const ui = studio(); await ui.flush();
      ui.el('visual-translation').checked = enabled;
      if (path === 'url') {
        ui.el('video-url').value = 'https://v.douyin.com/example/';
        await ui.el('video-url').emit('input');
      } else if (path === 'local-file') ui.window.loadDroppedLocalVideo('D:/clip.mp4');
      else {
        ui.el('video-file').files = [{ name: 'clip.mp4', size: 100 }];
        await ui.el('video-file').emit('change');
      }
      const endpoint = `/api/streaming/start-${path}`;
      ui.replies.set(endpoint, { task_id: 'visual-fixture' });
      await ui.el('btn-start').click();
      const request = ui.requests.find(item => item.url === endpoint);
      const body = path === 'upload' ? Object.fromEntries(request.options.body.parts.map(item => [item.name, item.value])) : JSON.parse(request.options.body);
      assert.equal(body.visual_translation, path === 'upload' ? String(enabled) : enabled);
      assert.equal(ui.el('visual-translation').disabled, true);
      ui.sockets.at(-1).receive({ type: 'finished', status: 'completed' });
      assert.equal(ui.el('visual-translation').disabled, false);
    }
  }
});

test('visual analysis progress describes analyzed duration separately from generated speech', async () => {
  const ui = studio(); await ui.start();
  ui.sockets.at(-1).receive({ type: 'progress', phase: 'visual', status: 'RUNNING', progress_pct: 40,
    stage: 'Đang đối chiếu lời thoại, phụ đề và tiêu đề' });
  assert.equal(ui.el('task-progress-value').textContent, '40%');
  assert.match(ui.el('task-progress-detail').textContent, /thời lượng video đã đối chiếu lời nói và chữ trên hình/);
  assert.doesNotMatch(ui.el('task-progress-detail').textContent, /số câu/);
  ui.sockets.at(-1).receive({ type: 'progress', phase: 'processing', status: 'RUNNING', progress_pct: 10 });
  assert.match(ui.el('task-progress-detail').textContent, /số câu/);
});

test('long translation appears as compact sequential pages with every word and sentence timing retained', async () => {
  const ui = studio(); await ui.start();
  const text = 'Đều quay bằng điện thoại thôi. Giả, hoàn toàn không có video bùng nổ trong một giây đâu. Chỉ cần bạn bình luận thì người khác có thể vào xem tài khoản của bạn.';
  ui.sockets.at(-1).receive({ type: 'segment_update', id: 0, start: 0, end: 10, status: 'READY', final_vi: text });
  const pages = [];
  for (let time = 0; time < 10; time += .05) {
    ui.el('video-player').currentTime = time;
    await ui.el('video-player').emit('timeupdate');
    const page = ui.el('subtitle-text').textContent;
    assert.ok(page, 'No subtitle gap inside the sentence');
    if (pages.at(-1) !== page) pages.push(page);
  }
  assert.ok(pages.length >= 3);
  assert.ok(pages.every(page => page.split('\n').length <= 2 && page.replaceAll('\n', ' ').length <= 60));
  assert.equal(pages.map(page => page.replaceAll('\n', ' ')).join(' '), text);
  ui.el('video-player').currentTime = 10;
  await ui.el('video-player').emit('timeupdate');
  assert.equal(ui.el('subtitle-text').textContent, '');
  ui.sockets.at(-1).receive({ type: 'segment_update', id: 0, start: 0, end: 10, status: 'READY', final_vi: 'Dòng một\nDòng hai' });
  ui.el('video-player').currentTime = 2;
  await ui.el('video-player').emit('timeupdate');
  assert.equal(ui.el('subtitle-text').textContent, 'Dòng một\nDòng hai');
});

function captionFixture(ui, overrides = {}) {
  const cues = overrides.cues || [
    { start: 0, end: 2, text: 'Câu hiện tại', bbox: [.10, .62, .80, .08], font_size: 54,
      video_size: [1080, 1920], background: 'yellow', border_radius: 6 },
    { start: 2, end: 4, text: 'Câu tiếp theo', bbox: [.12, .64, .76, .08], font_size: 54,
      video_size: [1080, 1920], background: 'yellow', border_radius: 6 },
  ];
  const segment = { id: 0, start: 0, end: 4, status: 'READY', final_vi: 'Câu hiện tại',
    audio_url: '/dub.wav', caption_layout: { video_size: [1080, 1920], cues },
    caption_bottom_layout: { video_size: [1080, 1920], cues: cues.map(cue => ({ ...cue,
      bbox: [.05, .84, .90, .08], background: 'white', border_radius: 0 })) }, ...overrides };
  ui.sockets.at(-1).receive({ type: 'init', duration: 4, segments_count: 1,
    visual_translation: true, screen_texts: [], segments: [segment] });
  return segment;
}

test('caption layout shows only the current measured cue and never translates source pixels', async () => {
  const ui = studio(); await ui.start();
  const video = ui.el('video-player'); video.videoWidth = 1080; video.videoHeight = 1920;
  video.src = '/source-original.mp4';
  ui.el('player-container').getBoundingClientRect = () => ({ width: 600, height: 640 });
  await video.emit('loadedmetadata');
  captionFixture(ui);
  assert.equal(video.src, '/source-original.mp4');
  assert.equal(ui.el('screen-text-overlay').children.length, 0);
  assert.equal(ui.el('subtitle-text').textContent, 'Câu hiện tại');
  assert.equal(ui.el('subtitle-text').style.background, '#FFE500');
  video.currentTime = 1.5; await video.emit('timeupdate');
  assert.equal(ui.el('subtitle-text').textContent, 'Câu hiện tại');
  video.currentTime = 2.1; await video.emit('timeupdate');
  assert.equal(ui.el('subtitle-text').textContent, 'Câu tiếp theo');
  video.currentTime = 4; await video.emit('timeupdate');
  assert.equal(ui.el('subtitle-text').textContent, '');
  assert.equal(ui.el('chinese-sub-mask').style.display, 'none');
});

test('caption layout uses measured yellow position or white bottom fallback without progressive text', async () => {
  const ui = studio(); await ui.start();
  const video = ui.el('video-player'); video.videoWidth = 1080; video.videoHeight = 1920;
  ui.el('player-container').getBoundingClientRect = () => ({ width: 600, height: 640 });
  await video.emit('loadedmetadata');
  captionFixture(ui);
  video.currentTime = 0; await video.emit('timeupdate');
  assert.equal(ui.el('subtitle-text').style.background, '#FFE500');
  assert.equal(ui.el('subtitle-overlay').style.left, '156px');
  assert.equal(ui.el('subtitle-overlay').style.top, '396.8px');
  ui.el('toggle-screen-text').checked = false; await ui.el('toggle-screen-text').emit('change');
  assert.equal(ui.el('subtitle-text').textContent, 'Câu hiện tại');
  assert.equal(ui.el('subtitle-text').style.background, '#FFFFFF');
  assert.equal(ui.el('subtitle-overlay').style.top, '537.6px');
  assert.equal(ui.el('subtitle-overlay').style.left, '138px');
  // A pause/seek does not reveal a future turn or progressively rewrite text.
  video.pause(); video.currentTime = 1; await video.emit('timeupdate');
  assert.equal(ui.el('subtitle-text').textContent, 'Câu hiện tại');
  video.currentTime = 2.1; await video.emit('timeupdate');
  assert.equal(ui.el('subtitle-text').textContent, 'Câu tiếp theo');
});

test('uncertain transcript remains editable, shows review reason and can confirm unchanged text', async () => {
  const ui = studio(); await ui.start();
  const segment = { id: 0, start: 0, end: 10, status: 'NEEDS_REVIEW', final_vi: 'Bản dịch cần kiểm tra',
    needs_review: true, review_reason: 'Lời nói và chữ trên hình chưa khớp.', revision: 0 };
  ui.sockets.at(-1).receive({ type: 'segment_update', ...segment });
  const row = ui.el('seg-row-0'), warning = row.querySelector('.transcript-review');
  assert.equal(warning.hidden, false);
  assert.ok(warning.textContent.startsWith(segment.review_reason));
  assert.match(warning.textContent, /AI sẽ đối chiếu lại lời gốc và bản dịch/);
  assert.equal(warning.getAttribute('role'), 'status');
  assert.equal(row.dataset.needsReview, 'true');
  assert.equal(ui.el('seg-vi-0').disabled, false);
  assert.equal(ui.el('btn-export-hq').disabled, true);
  await ui.el('video-player').play();
  assert.equal(ui.el('video-player').paused, true);
  assert.equal(ui.el('subtitle-text').textContent, '');
  assert.match(ui.el('buffering-text').textContent, /Đang chờ dịch và tạo giọng/);
  await ui.el('seg-vi-0').click();
  assert.equal(ui.el('seg-input-0').value, segment.final_vi);
  ui.replies.set('/api/streaming/fixture/segments/0', { segment: { ...segment, status: 'READY', needs_review: false,
    review_reason: '', revision: 1, audio_url: '/reviewed.wav' }, screen_texts: [] });
  await row.querySelector('.transcript-editor-actions').children[0].click();
  assert.equal(ui.requests.filter(request => request.options.method === 'PATCH').length, 1);
  assert.equal(warning.hidden, true);
  assert.equal(row.dataset.needsReview, 'false');
  assert.equal(ui.el('subtitle-text').textContent, segment.final_vi);
  assert.equal(ui.el('btn-export-hq').disabled, false);
});

test('ready draft keeps review visible while playing its dub and captions, then regenerates an edited sentence', async () => {
  const ui = studio(); await ui.start();
  const video = ui.el('video-player');
  const segment = captionFixture(ui, { needs_review: true, revision: 0,
    review_reason: 'Lời nhận dạng chưa rõ; nghe lại trước khi tạo giọng.' });
  const row = ui.el('seg-row-0'), warning = row.querySelector('.transcript-review');
  assert.equal(row.dataset.needsReview, 'true');
  assert.equal(ui.el('seg-badge-0').textContent, 'Bản nháp · Cần kiểm tra');
  assert.equal(ui.el('btn-export-hq').disabled, true, 'Draft preview must not silently accept an unresolved final export');
  assert.equal(warning.hidden, false);
  assert.match(warning.textContent, /AI kiểm tra lại/);
  assert.doesNotMatch(warning.textContent, /trước khi tạo giọng/);
  ui.sockets.at(-1).receive({ type: 'ready_to_play' });
  await video.play();
  video.currentTime = .8; await video.emit('timeupdate');
  const draftAudio = ui.audio.findLast(item => item.src === '/dub.wav');
  assert.equal(video.paused, false);
  assert.equal(draftAudio.paused, false);
  assert.equal(draftAudio.currentTime, .8);
  assert.equal(ui.el('subtitle-text').textContent, 'Câu hiện tại');
  assert.equal(ui.el('buffering-alert').classList.contains('hidden'), true);

  await ui.el('seg-vi-0').click();
  assert.equal(video.paused, true);
  const input = ui.el('seg-input-0'); input.value = 'Lời thoại đã sửa.'; await input.emit('input');
  const updated = { ...segment, needs_review: false, review_reason: '', revision: 1,
    final_vi: input.value, audio_url: '/edited-draft.wav',
    caption_layout: { ...segment.caption_layout, cues: [{ ...segment.caption_layout.cues[0], text: input.value }] } };
  ui.replies.set('/api/streaming/fixture/segments/0', { segment: updated });
  await row.querySelector('.transcript-editor-actions').children[0].click();
  const patch = ui.requests.find(item => item.options.method === 'PATCH');
  assert.deepEqual(JSON.parse(patch.options.body), { final_vi: 'Lời thoại đã sửa.' });
  assert.equal(warning.hidden, true);
  assert.equal(ui.el('subtitle-text').textContent, 'Lời thoại đã sửa.');
  assert.equal(ui.el('btn-export-hq').disabled, false);
  await video.play();
  assert.equal(ui.audio.find(item => item.src === '/edited-draft.wav').paused, false);
  assert.equal(draftAudio.paused, true);
});

test('empty ready review draft continues video without claiming a generated voice or allowing final export', async () => {
  const ui = studio(); await ui.start();
  const video = ui.el('video-player');
  ui.sockets.at(-1).receive({ type: 'segment_update', id: 0, start: 0, end: 10,
    status: 'READY', needs_review: true, final_vi: '', audio_url: null });
  const warning = ui.el('seg-row-0').querySelector('.transcript-review');
  assert.match(warning.textContent, /Chưa có giọng Việt.*video vẫn phát tiếp/);
  assert.match(warning.textContent, /AI kiểm tra lại/);
  assert.doesNotMatch(warning.textContent, /đã có giọng|Đang chuẩn bị giọng/);
  await video.play();
  assert.equal(video.paused, false);
  assert.equal(ui.el('subtitle-text').textContent, '');
  assert.equal(ui.audio.filter(item => item.src === '/dub.wav').every(item => item.paused), true);
  assert.equal(ui.el('btn-export-hq').disabled, true);
  assert.equal(ui.el('seg-vi-0').disabled, false);
});

test('original audition plays an uncertain source segment without translated audio or review gate and stops at its end', async () => {
  const ui = studio(); await ui.start();
  const video = ui.el('video-player'), socket = ui.sockets.at(-1);
  await video.play();
  const bgm = ui.audio.find(item => item.src === '/bgm.m4a'), dub = ui.audio.find(item => item.src === '/dub.wav');
  socket.receive({ type: 'segment_update', id: 1, start: 2, end: 4, duration: 2, status: 'NEEDS_REVIEW',
    needs_review: true, final_vi: 'Bản cần nghe lại', review_reason: 'Âm thanh chưa rõ.' });
  const button = ui.el('seg-row-1').querySelector('.transcript-listen-original');
  const bgmPlays = bgm.playCount, dubPlays = dub.playCount;
  await button.click();
  assert.equal(video.currentTime, 2);
  assert.equal(video.paused, false);
  assert.equal(video.muted, false);
  assert.equal(bgm.paused, true); assert.equal(dub.paused, true);
  assert.equal(button.getAttribute('aria-pressed'), 'true');
  assert.equal(ui.el('buffering-alert').classList.contains('hidden'), true);
  // A previously queued native pause event can arrive after play has started.
  await video.emit('pause');
  await video.emit('seeking'); await video.emit('seeked');
  video.currentTime = 3; await video.emit('timeupdate');
  await video.emit('play'); await video.emit('ratechange');
  assert.equal(video.paused, false);
  assert.equal(video.muted, false);
  assert.equal(bgm.playCount, bgmPlays); assert.equal(dub.playCount, dubPlays);
  assert.equal(ui.el('subtitle-text').textContent, '');
  video.currentTime = 4.02; await ui.tickIntervals(50);
  assert.equal(video.paused, true);
  assert.equal(video.currentTime, 4);
  assert.equal(video.muted, true);
  assert.equal(button.textContent, 'Nghe gốc');
  assert.equal(button.getAttribute('aria-pressed'), 'false');
  video.currentTime = 1;
  await video.play();
  assert.equal(dub.paused, false);
  assert.equal(bgm.paused, false);
  assert.equal(video.muted, true);
});

test('stopping original audition restores master mute, cancels its timer and permits another sentence', async () => {
  const ui = studio(); await ui.start();
  const video = ui.el('video-player');
  const first = ui.el('seg-row-0').querySelector('.transcript-listen-original');
  await ui.el('player-mute-toggle').click();
  await first.click();
  assert.equal(video.volume, 1);
  assert.equal(video.muted, false);
  await first.click();
  assert.equal(video.paused, true);
  assert.equal(video.volume, 0);
  assert.equal(video.muted, true);
  await first.click();
  ui.sockets.at(-1).receive({ type: 'segment_update', id: 1, start: 3, end: 5, status: 'NEEDS_REVIEW', needs_review: true, final_vi: 'Câu sau' });
  const next = ui.el('seg-row-1').querySelector('.transcript-listen-original');
  await next.click();
  assert.equal(first.getAttribute('aria-pressed'), 'false');
  assert.equal(next.getAttribute('aria-pressed'), 'true');
  assert.equal(video.currentTime, 3);
  video.pause(); await ui.flush();
  assert.equal(next.getAttribute('aria-pressed'), 'false');
  assert.equal(video.volume, 0);
  video.currentTime = 6; await ui.tickIntervals(50);
  assert.equal(video.currentTime, 6, 'Cancelled timer must not snap back to the old end');
});

test('source audition cleans up on source change, task stop and failed source playback', async () => {
  for (const action of ['source', 'stop', 'failure', 'pagehide']) {
    const ui = studio(); await ui.start();
    const video = ui.el('video-player'), button = ui.el('seg-row-0').querySelector('.transcript-listen-original');
    if (action === 'failure') video.play = async () => { throw new Error('source codec failed'); };
    await button.click();
    if (action === 'source') {
      ui.sockets.at(-1).receive({ type: 'finished', status: 'completed' });
      ui.window.loadDroppedLocalVideo('D:/next.mp4');
    } else if (action === 'stop') await ui.window.studioStop();
    else if (action === 'pagehide') await ui.window.emit('pagehide');
    assert.equal(video.paused, true);
    assert.equal(button.getAttribute('aria-pressed'), 'false');
    if (action === 'failure') assert.match(ui.el('transcript-status').textContent, /Không phát được âm thanh gốc/);
    video.currentTime = 12; await ui.tickIntervals(50);
    assert.equal(video.currentTime, 12);
  }
});

test('review silence requires its explicit action and becomes a ready silent interval without autoaccepting blank saves', async () => {
  const ui = studio(); await ui.start();
  const segment = { id: 0, start: 0, end: 10, status: 'NEEDS_REVIEW', needs_review: true,
    final_vi: '', text_zh: '', revision: 0, audio_url: null };
  ui.sockets.at(-1).receive({ type: 'segment_update', ...segment });
  const row = ui.el('seg-row-0'), silence = row.querySelector('.transcript-confirm-silence');
  assert.equal(silence.hidden, false);
  await ui.el('seg-vi-0').click();
  await row.querySelector('.transcript-editor-actions').children[0].click();
  assert.equal(ui.requests.filter(item => item.options.method === 'PATCH').length, 0);
  assert.match(row.querySelector('.transcript-edit-message').textContent, /Nhập bản dịch/);
  let resolve;
  ui.replies.set('/api/streaming/fixture/segments/0', () => new Promise(done => { resolve = done; }));
  const saving = silence.click(); await ui.flush(); await silence.click();
  assert.equal(silence.disabled, true);
  assert.equal(ui.el('btn-export-hq').disabled, true);
  const requests = ui.requests.filter(item => item.options.method === 'PATCH');
  assert.equal(requests.length, 1);
  assert.deepEqual(JSON.parse(requests[0].options.body), { final_vi: '', confirm_silence: true });
  resolve({ ok: true, json: async () => ({ segment: { ...segment, status: 'READY', needs_review: false,
    confirmed_silence: true, revision: 1 }, screen_texts: [] }) });
  await saving;
  assert.equal(silence.hidden, true);
  assert.equal(row.querySelector('.transcript-review').hidden, true);
  assert.equal(ui.el('seg-vi-0').textContent, 'Đã xác nhận không có lời thoại');
  assert.equal(ui.el('subtitle-text').textContent, '');
  assert.equal(ui.el('btn-export-hq').disabled, false);
  await ui.el('video-player').play();
  assert.equal(ui.el('video-player').paused, false);
  assert.equal(ui.audio.filter(item => item.src === '/dub.wav').every(item => item.paused), true);
  await ui.el('seg-vi-0').click();
  assert.equal(ui.el('seg-input-0').value, '', 'Status label must never become editable spoken text');
});

test('failed explicit silence confirmation preserves a typed draft and keeps the review unresolved', async () => {
  const ui = studio(); await ui.start();
  ui.sockets.at(-1).receive({ type: 'segment_update', id: 0, start: 0, end: 10, status: 'NEEDS_REVIEW',
    needs_review: true, final_vi: 'Nghi nhận nhầm', text_zh: '识别结果' });
  await ui.el('seg-vi-0').click();
  const input = ui.el('seg-input-0'); input.value = 'Bản đang sửa'; await input.emit('input');
  const row = ui.el('seg-row-0');
  ui.replies.set('/api/streaming/fixture/segments/0', { failure: true, detail: 'Đang xuất video, hãy thử lại sau.' });
  await row.querySelector('.transcript-confirm-silence').click();
  assert.equal(input.value, 'Bản đang sửa');
  assert.equal(input.disabled, false);
  assert.equal(row.querySelector('.transcript-confirm-silence').hidden, false);
  assert.match(row.querySelector('.transcript-edit-message').textContent, /Đang xuất video/);
  assert.equal(ui.el('seg-vi-0').textContent, 'Nghi nhận nhầm');
  assert.equal(ui.el('btn-export-hq').disabled, true);
});

test('source audition hides captions and restores the measured cue after speaker handover', async () => {
  const ui = studio(); await ui.start();
  captionFixture(ui);
  const video = ui.el('video-player'); video.currentTime = .5; await video.emit('timeupdate');
  assert.equal(ui.el('subtitle-text').textContent, 'Câu hiện tại');
  const listen = ui.el('seg-row-0').querySelector('.transcript-listen-original');
  await listen.click();
  assert.equal(ui.el('subtitle-text').textContent, '');
  video.currentTime = 1; await video.emit('timeupdate');
  assert.equal(ui.el('subtitle-text').textContent, '');
  await listen.click();
  video.currentTime = 2.1; await video.emit('timeupdate');
  assert.equal(ui.el('subtitle-text').textContent, 'Câu tiếp theo');
  assert.equal(ui.el('screen-text-overlay').children.length, 0);
  assert.equal(ui.el('chinese-sub-mask').style.display, 'none');
});

test('caption layout keeps the complete current utterance instead of progressive karaoke', async () => {
  const ui = studio(); await ui.start();
  captionFixture(ui, { cues: [{ start: 0, end: 3, text: 'Đây là trọn câu hiện tại', bbox: [.08, .62, .84, .10],
    font_size: 52, video_size: [1080, 1920], background: 'yellow', border_radius: 4 }] });
  const video = ui.el('video-player'), seen = [];
  for (const time of [0, .4, 1.2, 2.9]) {
    video.currentTime = time; await video.emit('timeupdate');
    if (seen.at(-1) !== ui.el('subtitle-text').textContent) seen.push(ui.el('subtitle-text').textContent);
  }
  assert.deepEqual(seen, ['Đây là trọn câu hiện tại']);
});

test('caption edits replace text and layout together and ignore a stale websocket revision', async () => {
  const ui = studio(); await ui.start();
  const segment = captionFixture(ui);
  const updated = {...segment, revision: 1, final_vi: 'Lời thoại đã sửa.',
    caption_layout: {...segment.caption_layout, cues: [{...segment.caption_layout.cues[0],
      text: 'Lời thoại đã sửa.', bbox: [.2, .7, .6, .08]}]}};
  ui.sockets.at(-1).receive({type: 'segment_update', ...updated});
  assert.equal(ui.el('subtitle-text').textContent, 'Lời thoại đã sửa.');
  assert.equal(ui.el('seg-vi-0').textContent, 'Lời thoại đã sửa.');
  ui.sockets.at(-1).receive({type: 'segment_update', ...segment, revision: 0});
  assert.equal(ui.el('subtitle-text').textContent, 'Lời thoại đã sửa.');
  assert.equal(ui.el('screen-text-overlay').children.length, 0);
});

test('transcript reports selected text provider and keeps review status prominent', async () => {
  const ui = studio(); await ui.start();
  const segment = { id: 0, start: 0, end: 10, status: 'READY', final_vi: 'Bản đã đối chiếu.' };
  ui.sockets.at(-1).receive({ type: 'segment_update', ...segment, source_method: 'text-ai', translation_provider: 'openrouter-free' });
  assert.equal(ui.el('seg-badge-0').textContent, 'Sẵn sàng · OpenRouter · bản chép + OCR');
  ui.sockets.at(-1).receive({ type: 'segment_update', ...segment, source_method: 'text-ai', translation_provider: 'opencode' });
  assert.equal(ui.el('seg-badge-0').textContent, 'Sẵn sàng · OpenCode · bản chép + OCR');
  ui.sockets.at(-1).receive({ type: 'segment_update', ...segment, source_method: 'video-ai' });
  assert.equal(ui.el('seg-badge-0').textContent, 'Sẵn sàng · AI hình + tiếng');
  ui.sockets.at(-1).receive({ type: 'segment_update', ...segment, source_method: 'text-ai', status: 'NEEDS_REVIEW', needs_review: true });
  assert.equal(ui.el('seg-badge-0').textContent, 'Cần kiểm tra');
  assert.equal(ui.el('seg-row-0').querySelector('.transcript-review').hidden, false);
});

test('caption export sends the position mode while the source mask stays false', async () => {
  for (const positioned of [true, false]) {
    const ui = studio(); await ui.start();
    ui.el('toggle-screen-text').checked = positioned; await ui.el('toggle-screen-text').emit('change');
    await ui.el('btn-export-hq').click();
    const summary = ui.el('export-screen-text-summary').textContent;
    assert.match(summary, positioned ? /giữ sub Trung.*nền vàng/i : /Giữ nguyên chữ và hình nguồn/);
    assert.doesNotMatch(summary, /che vùng|làm mờ/i);
    ui.replies.set('/api/streaming/export-hq', { video_url: '/outputs/final.mp4' });
    await ui.el('btn-confirm-export').click();
    const request = ui.requests.find(item => item.url === '/api/streaming/export-hq');
    const body = JSON.parse(request.options.body);
    assert.equal(body.translate_screen_text, positioned);
    assert.equal(body.mask_chinese, false);
  }
});

test('entering a confirmed silent row stops the previous dub and resuming speech creates the next track', async () => {
  const ui = studio(); await ui.start();
  ui.sockets.at(-1).receive({type:'init',duration:6,segments_count:3,segments:[
    {id:0,start:0,end:2,duration:2,status:'READY',final_vi:'Câu trước',audio_url:'/first.wav'},
    {id:1,start:2,end:4,duration:2,status:'READY',confirmed_silence:true,final_vi:'',audio_url:null},
    {id:2,start:4,end:6,duration:2,status:'READY',final_vi:'Câu sau',audio_url:'/next.wav'},
  ]});
  const video = ui.el('video-player');
  await video.play();
  const first = ui.audio.find(item => item.src === '/first.wav');
  assert.equal(first.paused, false);
  video.currentTime = 2.1; await video.emit('timeupdate');
  assert.equal(first.paused, true);
  assert.equal(video.paused, false);
  assert.equal(ui.el('subtitle-text').textContent, '');
  video.currentTime = 4.1; await video.emit('timeupdate');
  assert.equal(first.paused, true);
  assert.equal(ui.audio.find(item => item.src === '/next.wav').paused, false);
});

test('reconnect snapshot replaces an edited audio revision at the current paused position', async () => {
  const ui = studio(); await ui.start();
  const video = ui.el('video-player'); video.currentTime = 1.1; await video.emit('timeupdate');
  const original = ui.audio.find(item => item.src === '/dub.wav');
  ui.sockets.at(-1).receive({type:'init',duration:10,segments_count:1,segments:[
    {id:0,start:0,end:10,duration:10,status:'READY',revision:2,final_vi:'Bản sửa mới',audio_url:'/dub.wav?rev=2'},
  ]});
  const revised = ui.audio.find(item => item.src === '/dub.wav?rev=2');
  assert.equal(original.paused, true);
  assert.ok(revised);
  assert.equal(revised.currentTime, 1.1);
  assert.equal(revised.paused, true);
  assert.equal(ui.el('subtitle-text').textContent, 'Bản sửa mới');
  await video.play();
  assert.equal(revised.paused, false);
  assert.equal(original.paused, true);
});


test('retry resumes failed speech without starting or translating the video again', async () => {
  const ui = studio(); await ui.start();
  const socket = ui.sockets.at(-1);
  const video = ui.el('video-player');
  socket.receive({type:'ready_to_play'});
  video.currentTime = 4.5;
  socket.receive({type:'progress',status:'FAILED',can_retry:true,stage:'TTS unavailable',progress_pct:36.4});
  assert.equal(ui.el('btn-retry-worker').classList.contains('hidden'), false);
  const before = ui.requests.filter(r=>r.url.includes('/start-')).length;
  ui.replies.set('/api/streaming/fixture/retry', {task_id:'fixture',status:'retrying',progress:{status:'RUNNING',phase:'tts',can_retry:false,progress_pct:36.4}});
  await ui.el('btn-retry-worker').click();
  assert.equal(ui.requests.filter(r=>r.url.includes('/start-')).length,before);
  assert.equal(ui.requests.find(r=>r.url.endsWith('/retry')).options.method,'POST');
  assert.equal(ui.el('btn-retry-worker').classList.contains('hidden'),true);
  assert.equal(ui.sockets.length,1);
  assert.equal(video.currentTime,4.5);
  assert.equal(video.paused,false);
  assertWorkerSourceBusy(ui,true);
  assert.equal(ui.el('task-progress').dataset.status,'RUNNING');
});

function assertWorkerSourceBusy(ui, busy) {
  assert.equal(ui.el('btn-start').classList.contains('hidden'),busy);
  assert.equal(ui.el('video-url').disabled,busy);
  assert.equal(ui.el('video-file').disabled,busy);
  assert.equal(ui.el('drop-zone').getAttribute('aria-disabled'),String(busy));
  assert.equal(ui.el('voice-select').disabled,busy);
  assert.equal(ui.el('visual-translation').disabled,busy);
  for (const row of ui.el('voice-list').querySelectorAll('.voice-row')) {
    if (row.dataset.voiceId === 'vieneu:Unavailable') continue;
    assert.equal(row.querySelector('.voice-choose-button').disabled,busy);
    if (busy) assert.equal(row.querySelector('.voice-preview-button').disabled,true);
  }
}

test('retry locks source and voice during its request and restores them on rejection', async () => {
  const ui = studio(undefined,{catalog:voiceCatalog}); await ui.start();
  ui.sockets.at(-1).receive({type:'progress',status:'FAILED',can_retry:true,stage:'TTS unavailable'});
  let finish;
  ui.replies.set('/api/streaming/fixture/retry',()=>new Promise(resolve=>{finish=resolve;}));
  const pending = ui.el('btn-retry-worker').click();
  assertWorkerSourceBusy(ui,true);
  const starts = ui.requests.filter(item=>item.url.includes('/start-')).length;
  await ui.el('btn-start').click();
  ui.window.loadDroppedLocalVideo('D:/another.mp4');
  assert.equal(ui.window.currentLocalFilePath,'D:/clip.mp4');
  assert.equal(ui.requests.filter(item=>item.url.includes('/start-')).length,starts);
  // A task poll during the pending request must not unlock the controls.
  ui.sockets.at(-1).receive({type:'progress',status:'FAILED',can_retry:true});
  assertWorkerSourceBusy(ui,true);
  finish({ok:false,json:async()=>({detail:'Đang lưu câu thoại.'})});
  await pending;
  assertWorkerSourceBusy(ui,false);
  assert.equal(ui.el('btn-retry-worker').disabled,false);
  assert.equal(ui.alerts.at(-1),'Đang lưu câu thoại.');
});

test('retry reconnect preserves compatible preview, playhead and existing background audio', async () => {
  const ui = studio(); await ui.start();
  const video = ui.el('video-player');
  const oldSocket = ui.sockets.at(-1);
  ui.replies.set('/api/preview',{status:'READY',video_url:'/api/preview/compatible.mp4'});
  video.error = {code:4};
  await video.emit('error'); await ui.flush();
  await video.emit('loadedmetadata'); await video.emit('canplay');
  video.currentTime = 4.5;
  oldSocket.receive({type:'progress',status:'FAILED',can_retry:true,stage:'TTS unavailable'});
  oldSocket.close();
  ui.replies.set('/api/streaming/fixture/retry',{progress:{status:'RUNNING',can_retry:false}});
  const loads = video.loadCount;
  const bgm = ui.audio.find(item=>item.src==='/bgm.m4a');
  await ui.el('btn-retry-worker').click();
  assert.equal(ui.sockets.length,2);
  const socket = ui.sockets.at(-1);
  socket.receive({type:'source_ready',video_url:'/fixture.mp4'});
  socket.receive({type:'init',duration:10,segments_count:1,bgm_url:'/bgm.m4a',segments:[
    {id:0,start:0,end:10,duration:10,status:'READY',audio_url:'/dub.wav',final_vi:'Xin chào'}]});
  assert.equal(video.src,'/api/preview/compatible.mp4');
  assert.equal(video.currentTime,4.5);
  assert.equal(video.loadCount,loads);
  assert.equal(ui.audio.filter(item=>item.src==='/bgm.m4a').length,1);
  assert.ok(ui.audio.includes(bgm));
});

test('late retry response cannot overwrite a terminal result already delivered by its live socket', async () => {
  for (const status of ['COMPLETED','FAILED']) {
    const ui = studio(); await ui.start();
    const socket = ui.sockets.at(-1);
    socket.receive({type:'progress',status:'FAILED',can_retry:true});
    let finish;
    ui.replies.set('/api/streaming/fixture/retry',()=>new Promise(resolve=>{finish=resolve;}));
    const pending = ui.el('btn-retry-worker').click();
    socket.receive({type:'progress',status:'RUNNING',can_retry:false});
    socket.receive({type:'progress',status,can_retry:status==='FAILED',progress_pct:100});
    finish({ok:true,json:async()=>({progress:{status:'RUNNING',can_retry:false}})});
    await pending;
    assert.equal(ui.el('task-progress').dataset.status,status);
    assertWorkerSourceBusy(ui,false);
  }
});

test('an old failure snapshot during retry does not suppress its successful response', async () => {
  const ui = studio(); await ui.start();
  const socket = ui.sockets.at(-1);
  socket.receive({type:'progress',status:'FAILED',can_retry:true});
  let finish;
  ui.replies.set('/api/streaming/fixture/retry',()=>new Promise(resolve=>{finish=resolve;}));
  const pending = ui.el('btn-retry-worker').click();
  socket.receive({type:'progress',status:'FAILED',can_retry:true});
  finish({ok:true,json:async()=>({progress:{status:'RUNNING',can_retry:false}})});
  await pending;
  assert.equal(ui.el('task-progress').dataset.status,'RUNNING');
  assertWorkerSourceBusy(ui,true);
});

test('attach restores an existing task without source download or model requests', async () => {
  const ui = studio(); await ui.flush();
  ui.replies.set('/api/streaming/recovered', {video_url:'/kept.mp4',progress:{status:'FAILED',can_retry:true}});
  assert.equal(await ui.window.studioAttachTask('recovered'),true);
  assert.equal(ui.el('video-player').src,'/kept.mp4');
  assert.equal(ui.requests.filter(r=>r.url.includes('/start-')).length,0);
  assert.equal(ui.sockets.length,1);
  assert.equal(ui.el('btn-retry-worker').classList.contains('hidden'),false);
  assertWorkerSourceBusy(ui,false);
});

test('attach locks controls while loading and keeps active sessions locked', async () => {
  const ui = studio(undefined,{catalog:voiceCatalog}); await ui.flush();
  let finish;
  ui.replies.set('/api/streaming/recovered',()=>new Promise(resolve=>{finish=resolve;}));
  const pending = ui.window.studioAttachTask('recovered');
  assertWorkerSourceBusy(ui,true);
  finish({ok:true,json:async()=>({video_url:'/kept.mp4',progress:{status:'RUNNING'}})});
  assert.equal(await pending,true);
  assertWorkerSourceBusy(ui,true);
  const video = ui.el('video-player'); video.currentTime=3;
  ui.sockets.at(-1).receive({type:'source_ready',video_url:'/kept.mp4'});
  assert.equal(video.currentTime,3);
  ui.sockets.at(-1).receive({type:'progress',status:'COMPLETED',progress_pct:100});
  assertWorkerSourceBusy(ui,false);
});

test('failed attach restores idle source controls', async () => {
  const ui = studio(undefined,{catalog:voiceCatalog}); await ui.flush();
  ui.replies.set('/api/streaming/missing',{failure:true});
  await assert.rejects(ui.window.studioAttachTask('missing'),/Phiên dịch không còn tồn tại/);
  assertWorkerSourceBusy(ui,false);
  assert.equal(ui.sockets.length,0);
});

test('AI review uses the existing task and preserves playback while locking source, transcript and export', async () => {
  const ui = studio(undefined, {catalog:voiceCatalog}); await ui.start();
  const video = ui.el('video-player'), socket = ui.sockets.at(-1);
  socket.receive({type:'progress',status:'COMPLETED',can_review:true,progress_pct:100});
  await video.play(); video.currentTime=4.5;
  const audio = ui.audio.find(item=>item.src==='/dub.wav');
  assert.equal(ui.el('btn-review-worker').classList.contains('hidden'),false);
  const starts = ui.requests.filter(item=>item.url.includes('/start-')).length;
  let finish;
  ui.replies.set('/api/streaming/fixture/review',()=>new Promise(resolve=>{finish=resolve;}));
  const pending=ui.el('btn-review-worker').click();
  assertWorkerSourceBusy(ui,true);
  assert.equal(ui.el('btn-export-hq').disabled,true);
  assert.equal(ui.el('seg-vi-0').disabled,true);
  assert.equal(ui.el('btn-review-worker').disabled,true);
  await ui.el('btn-review-worker').click();
  await ui.el('btn-start').click();
  ui.window.loadDroppedLocalVideo('D:/another.mp4');
  assert.equal(ui.window.currentLocalFilePath,'D:/clip.mp4');
  assert.equal(video.paused,false);
  assert.equal(video.currentTime,4.5);
  assert.equal(audio.paused,false);
  assert.equal(ui.requests.filter(item=>item.url.endsWith('/review')).length,1);
  finish({ok:true,json:async()=>({task_id:'fixture',status:'reviewing',progress:{status:'RUNNING',phase:'review',can_review:false,review_summary:{status:'running',checked:0}}})});
  await pending;
  assertWorkerSourceBusy(ui,true);
  assert.equal(ui.el('btn-export-hq').disabled,true);
  assert.equal(ui.el('seg-vi-0').disabled,true);
  assert.equal(ui.sockets.length,1);
  assert.equal(ui.requests.filter(item=>item.url.includes('/start-')).length,starts);
  assert.match(ui.el('task-progress-detail').textContent,/AI đang đối chiếu và sửa bản dịch/);
});

test('AI review receives corrected audio revision at the current playhead and reports verified and unresolved results honestly', async () => {
  const ui=studio(); await ui.start();
  const socket=ui.sockets.at(-1), video=ui.el('video-player');
  const segment={id:0,start:0,end:10,duration:10,status:'READY',final_vi:'Bản cũ',audio_url:'/dub.wav',revision:0,needs_review:true};
  socket.receive({type:'segment_update',...segment});
  socket.receive({type:'progress',status:'RUNNING',phase:'review',can_review:false,review_summary:{status:'running',checked:0}});
  await video.play(); video.currentTime=2; await video.emit('timeupdate');
  const original=ui.audio.find(item=>item.src==='/dub.wav');
  const corrected={...segment,final_vi:'Bản đã sửa',needs_review:false,audio_url:'/corrected.wav?revision=1',revision:1,verification:{status:'corrected',reason:'Đối chiếu với câu gốc.'}};
  socket.receive({type:'segment_update',...corrected});
  assert.match(ui.el('seg-badge-0').textContent,/AI đã sửa/);
  assert.equal(ui.el('seg-row-0').querySelector('.transcript-review').hidden,true);
  assert.equal(original.paused,true);
  assert.equal(ui.audio.find(item=>item.src===corrected.audio_url).currentTime,2);
  assert.equal(ui.audio.find(item=>item.src===corrected.audio_url).paused,false);
  socket.receive({type:'segment_update',...segment});
  assert.equal(ui.el('seg-vi-0').textContent,'Bản đã sửa');
  socket.receive({type:'review_complete',review_summary:{status:'completed',checked:1,verified:0,corrected:1,unresolved:0}});
  socket.receive({type:'progress',status:'COMPLETED',phase:'complete',can_review:true,review_count:0,progress_pct:100});
  assertWorkerSourceBusy(ui,false);
  assert.equal(ui.el('btn-export-hq').disabled,false);
  assert.equal(ui.el('seg-vi-0').disabled,false);
  assert.match(ui.el('transcript-status').textContent,/AI đã kiểm tra 1 câu.*Đã sửa 1/);
  socket.receive({type:'segment_update',...corrected,verification:{status:'verified'},revision:2});
  assert.match(ui.el('seg-badge-0').textContent,/AI đã kiểm tra/);
  socket.receive({type:'segment_update',...corrected,revision:3,needs_review:true,verification:{status:'unresolved',reason:'Tiếng bị nhạc che và chữ gốc bị cắt mất.'}});
  socket.receive({type:'progress',status:'COMPLETED',can_review:true,review_count:1,review_summary:{status:'completed',checked:1,verified:0,corrected:0,unresolved:1}});
  assert.match(ui.el('seg-badge-0').textContent,/AI chưa xác minh được/);
  assert.match(ui.el('seg-row-0').querySelector('.transcript-review').textContent,/Tiếng bị nhạc che và chữ gốc bị cắt mất/);
  assert.doesNotMatch(ui.el('seg-row-0').querySelector('.transcript-review').textContent,/nghe thử rồi|nghe và sửa/);
  assert.match(ui.el('task-progress-detail').textContent,/Còn 1 câu nguồn chưa rõ/);
  assert.match(ui.el('task-progress-detail').textContent,/Không cần xác nhận thủ công/);
  assert.equal(ui.el('btn-export-hq').disabled,false, 'An explicitly audited unresolved source may export with its uncertainty retained');
  assert.equal(ui.el('seg-row-0').dataset.needsReview,'true');
  socket.receive({type:'segment_update',...corrected,revision:4,needs_review:true,verification:null});
  assert.equal(ui.el('btn-export-hq').disabled,true, 'An unaudited uncertain row remains blocked');
});

test('rejected AI review restores completed controls without discarding its draft or audio', async () => {
  const ui=studio(); await ui.start();
  ui.sockets.at(-1).receive({type:'progress',status:'COMPLETED',can_review:true});
  const video=ui.el('video-player'); video.currentTime=5;
  ui.replies.set('/api/streaming/fixture/review',{failure:true,detail:'Dịch vụ AI đang bận.'});
  await ui.el('btn-review-worker').click();
  assert.equal(ui.alerts.at(-1),'Dịch vụ AI đang bận.');
  assertWorkerSourceBusy(ui,false);
  assert.equal(ui.el('seg-vi-0').disabled,false);
  assert.equal(ui.el('btn-review-worker').disabled,false);
  assert.equal(ui.el('btn-export-hq').disabled,false);
  assert.equal(video.currentTime,5);
  assert.equal(ui.el('seg-vi-0').textContent,'<b>Xin chào</b>');
});

test('AI review cannot overwrite unsaved transcript edits or run alongside an export', async () => {
  const ui=studio(); await ui.start();
  ui.sockets.at(-1).receive({type:'progress',status:'COMPLETED',can_review:true});
  await ui.el('seg-vi-0').click();
  ui.el('seg-input-0').value='Bản đang sửa'; await ui.el('seg-input-0').emit('input');
  assert.equal(ui.el('btn-review-worker').disabled,true);
  await ui.window.studioReview();
  assert.equal(ui.requests.filter(item=>item.url.endsWith('/review')).length,0);
  assert.equal(ui.el('seg-input-0').value,'Bản đang sửa');
  await ui.el('seg-row-0').querySelector('.transcript-editor-actions').children[1].click();
  let finish;
  ui.replies.set('/api/streaming/export-hq',()=>new Promise(resolve=>{finish=resolve;}));
  await ui.el('btn-export-hq').click();
  const pending=ui.el('btn-confirm-export').click();
  assert.equal(ui.el('btn-review-worker').disabled,true);
  await ui.window.studioReview();
  assert.equal(ui.requests.filter(item=>item.url.endsWith('/review')).length,0);
  finish({ok:true,json:async()=>({video_url:'/result.mp4'})}); await pending;
  assert.equal(ui.el('btn-review-worker').disabled,false);
});

test('late AI review response does not overwrite completed websocket review or reset its audio', async () => {
  const ui=studio(); await ui.start();
  const socket=ui.sockets.at(-1);
  socket.receive({type:'progress',status:'COMPLETED',can_review:true});
  let finish;
  ui.replies.set('/api/streaming/fixture/review',()=>new Promise(resolve=>{finish=resolve;}));
  const pending=ui.window.studioReview();
  const summary={status:'completed',checked:1,verified:1,corrected:0,unresolved:0};
  socket.receive({type:'review_complete',review_summary:summary});
  socket.receive({type:'progress',status:'COMPLETED',phase:'complete',can_review:true,review_summary:summary});
  finish({ok:true,json:async()=>({progress:{status:'RUNNING',phase:'review',review_summary:{status:'running',checked:0}}})});
  await pending;
  assert.equal(ui.el('task-progress').dataset.status,'COMPLETED');
  assert.match(ui.el('task-progress-detail').textContent,/AI đã kiểm tra 1 câu/);
  assertWorkerSourceBusy(ui,false);
  assert.equal(ui.el('btn-export-hq').disabled,false);
});

test('AI review reconnect retains preview and existing audio and reports failed review without claiming verification', async () => {
  const ui=studio(); await ui.start();
  const socket=ui.sockets.at(-1), video=ui.el('video-player');
  socket.receive({type:'progress',status:'COMPLETED',can_review:true});
  video.currentTime=3.25; socket.close();
  const loads=video.loadCount, audio=ui.audio.find(item=>item.src==='/dub.wav');
  ui.replies.set('/api/streaming/fixture/review',{progress:{status:'RUNNING',phase:'review',review_summary:{status:'running',checked:0}}});
  await ui.window.studioReview();
  assert.equal(ui.sockets.length,2);
  assert.equal(video.currentTime,3.25);
  assert.equal(video.loadCount,loads);
  assert.ok(ui.audio.includes(audio));
  ui.sockets.at(-1).receive({type:'progress',status:'COMPLETED',phase:'complete',can_review:true,review_summary:{status:'failed',checked:0}});
  assert.match(ui.el('task-progress-detail').textContent,/AI chưa hoàn tất kiểm tra/);
  assert.equal(ui.el('btn-export-hq').disabled,true);
  assert.equal(ui.el('btn-review-worker').disabled,false);
  assertWorkerSourceBusy(ui,false);
});

test('backend automatic export locks edits and displays a final result without autoplay or posting a second export', async () => {
  const ui=studio(); await ui.start();
  const socket=ui.sockets.at(-1), video=ui.el('video-player');
  socket.receive({type:'progress',status:'COMPLETED',can_review:true,review_summary:{status:'completed',checked:1,verified:1,corrected:0,unresolved:0}});
  video.pause(); video.currentTime=3;
  const plays=video.playCount, loads=video.loadCount;
  socket.receive({type:'export_progress',progress:35,stage:'Đang ghép video và giọng đọc'});
  assertWorkerSourceBusy(ui,true);
  assert.equal(ui.el('btn-export-hq').disabled,true);
  assert.equal(ui.el('seg-vi-0').disabled,true);
  assert.equal(ui.el('btn-review-worker').disabled,true);
  assert.equal(ui.el('task-progress-value').textContent,'35%');
  assert.match(ui.el('task-progress-detail').textContent,/Đang tạo video kết quả/);
  await ui.el('seg-vi-0').click();
  assert.equal(ui.el('seg-row-0').querySelector('.transcript-editor').hidden,true);
  socket.receive({type:'result_ready',output_video_url:'/api/outputs/reviewed.mp4',output_filename:'reviewed.mp4',review_summary:{status:'completed',checked:1,verified:1,corrected:0,unresolved:0}});
  assertWorkerSourceBusy(ui,false);
  assert.equal(ui.el('seg-vi-0').disabled,false);
  assert.equal(ui.el('btn-export-hq').disabled,false);
  assert.equal(ui.el('task-result').classList.contains('hidden'),false);
  assert.equal(ui.el('task-result-link').href,'http://localhost/api/outputs/reviewed.mp4');
  assert.match(ui.el('task-result-status').textContent,/reviewed\.mp4/);
  assert.equal(ui.el('btn-save-result').classList.contains('hidden'),false);
  assert.equal(video.src,'/fixture.mp4');
  assert.equal(video.currentTime,3);
  assert.equal(video.paused,true);
  assert.equal(video.playCount,plays);
  assert.equal(video.loadCount,loads);
  assert.equal(ui.requests.filter(item=>item.url==='/api/streaming/export-hq').length,0);
  const saved=[];
  ui.window.desktopBridge={saveVideoAs:(name,callback)=>{saved.push(name);callback('D:/saved.mp4');}};
  await ui.el('btn-save-result').click();
  assert.deepEqual(saved,['reviewed.mp4']);
});

test('failed backend export releases controls and preserves playable reviewed transcript for retry', async () => {
  const ui=studio(); await ui.start();
  const socket=ui.sockets.at(-1);
  socket.receive({type:'export_progress',progress:45});
  socket.receive({type:'result_error',message:'Không đủ dung lượng trống để xuất.'});
  assertWorkerSourceBusy(ui,false);
  assert.equal(ui.el('btn-export-hq').disabled,false);
  assert.equal(ui.el('seg-vi-0').disabled,false);
  assert.equal(ui.el('seg-vi-0').textContent,'<b>Xin chào</b>');
  assert.match(ui.el('task-result-status').textContent,/Không đủ dung lượng/);
  assert.equal(ui.el('task-result-link').classList.contains('hidden'),true);
  assert.equal(ui.requests.filter(item=>item.url==='/api/streaming/export-hq').length,0);
});

test('attached result restores the output link and selecting a different source removes the old result', async () => {
  const ui=studio(); await ui.flush();
  ui.replies.set('/api/streaming/recovered',{video_url:'/kept.mp4',output_video_url:'/api/outputs/final.mp4',output_filename:'final.mp4',progress:{status:'COMPLETED'}});
  await ui.window.studioAttachTask('recovered');
  assert.equal(ui.el('task-result-link').href,'http://localhost/api/outputs/final.mp4');
  assert.equal(ui.el('video-player').playCount,0);
  ui.window.loadDroppedLocalVideo('D:/another.mp4');
  assert.equal(ui.el('task-result').classList.contains('hidden'),true);
  assert.equal(ui.el('task-result-link').href,undefined);
});

test('task polling follows active auto export instead of resetting its progress to the finished translation', async () => {
  const ui=studio(); await ui.start();
  const socket=ui.sockets.at(-1);
  socket.receive({type:'progress',status:'COMPLETED',can_review:true});
  socket.receive({type:'export_progress',progress:25,stage:'Đang xuất 25%'});
  const translation={task_id:'fixture',status:'COMPLETED',phase:'complete',stage:'Đã dịch xong',can_review:true,progress_pct:100};
  ui.replies.set('/api/tasks',{tasks:[translation,{task_id:'export_fixture',status:'RUNNING',progress_pct:45,stage:'Đang xuất 45%'}]});
  await ui.tickIntervals(2000);
  assert.equal(ui.el('task-progress-value').textContent,'45%');
  assertWorkerSourceBusy(ui,true);
  assert.equal(ui.el('seg-vi-0').disabled,true);
  ui.replies.set('/api/tasks',{tasks:[translation,{task_id:'export_fixture',status:'COMPLETED',output_video_url:'/api/outputs/polled.mp4',output_filename:'polled.mp4',progress_pct:100}]});
  await ui.tickIntervals(2000);
  assertWorkerSourceBusy(ui,false);
  assert.equal(ui.el('seg-vi-0').disabled,false);
  assert.equal(ui.el('task-result-link').href,'http://localhost/api/outputs/polled.mp4');
});

test('result invalidation removes old output and exposes manual changes without relabeling them AI verified', async () => {
  const ui=studio(); await ui.start();
  const socket=ui.sockets.at(-1), video=ui.el('video-player');
  socket.receive({type:'result_ready',output_video_url:'/api/outputs/old.mp4',output_filename:'old.mp4'});
  video.currentTime=2;
  socket.receive({type:'result_invalidated',reason:'transcript_changed',output_video_url:'',output_filename:'',
    review_summary:{status:'completed',checked:0,verified:0,corrected:0,unresolved:0,manual:1}});
  assert.equal(ui.el('task-result').classList.contains('hidden'),true);
  assert.equal(ui.el('task-result-link').href,undefined);
  assert.match(ui.el('transcript-status').textContent,/1 câu do bạn sửa sau kiểm tra/);
  assert.doesNotMatch(ui.el('transcript-status').textContent,/Bản dịch đã qua kiểm tra tự động/);
  assert.equal(video.currentTime,2);
  const saved=[]; ui.window.desktopBridge={saveVideoAs:name=>saved.push(name)};
  await ui.el('btn-save-result').click();
  assert.equal(saved.length,0);
  socket.receive({type:'result_invalidated',reason:'review_started',review_summary:{status:'running'}});
  assert.equal(ui.el('btn-export-hq').disabled,true);
  assertWorkerSourceBusy(ui,true);
});
