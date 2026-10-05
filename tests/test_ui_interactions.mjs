import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

const source = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
const template = readFileSync(new URL('../templates/index.html', import.meta.url), 'utf8');

function studio(cookieReply = { configured: false, count: 0, message: '' }, voiceConfig = {}) {
  const elements = new Map(), audio = [], sockets = [], requests = [], alerts = [], copied = [];
  let ready;
  class Element {
    constructor(id = '') {
      this.id = id; this.events = {}; this.children = []; this.style = {}; this.dataset = {};
      this.value = ''; this.files = []; this.checked = true;
      this.paused = true; this.currentTime = 0; this.playbackRate = 1; this.ended = false;
      this.playCount = 0; this.classes = new Set(); this.hidden = false; this.disabled = false;
      this.classList = {
        add: (...names) => names.forEach(name => this.classes.add(name)),
        remove: (...names) => names.forEach(name => this.classes.delete(name)),
        contains: name => this.classes.has(name),
        toggle: (name, enabled) => enabled ? this.classes.add(name) : this.classes.delete(name),
      };
    }
    addEventListener(name, callback) { (this.events[name] ||= []).push(callback); }
    async emit(name, data = {}) { for (const callback of this.events[name] || []) await callback({ target: this, preventDefault() {}, stopPropagation() {}, ...data }); }
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
    load() { this.currentTime = 0; }
    pause() { this.paused = true; this.emit('pause'); }
    async play() { this.paused = false; this.playCount++; await this.emit('play'); }
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
  for (const [id, value] of Object.entries({ 'vol-dub': '1', 'vol-bgm': '0.3', 'buffer-select': '10', 'voice-select': 'vi-VN-HoaiMyNeural' })) el(id).value = value;
  const document = {
    getElementById: el,
    createElement: tag => { const element = new Element(); element.tagName = tag.toUpperCase(); return element; }, querySelectorAll: () => [],
    addEventListener: (_, callback) => { ready = callback; },
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
    document, window, Audio, WebSocket, fetch, console, alert: value => alerts.push(value),
    Option: function(text, value) { this.text = text; this.value = value; },
    FormData: class {
      constructor() { this.parts = []; }
      append(name, value, filename) { this.parts.push({ name, value, filename }); }
    },
    URL: Object.assign(class extends URL {}, { createObjectURL: () => 'blob:video', revokeObjectURL() {} }),
    navigator: { clipboard: { writeText: async text => { copied.push(text); } } },
    localStorage: { removeItem(key) { saved.delete(key); }, getItem(key) { return saved.get(key); }, setItem(key, value) { saved.set(key, value); } },
    setTimeout() {}, setInterval() { return 1; }, clearInterval() {},
  });
  ready();
  const flush = () => new Promise(resolve => setImmediate(resolve));
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
  return { el, row, choose, preview, audio, sockets, requests, replies, alerts, copied, window, flush, start, saved };
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
  assert.equal(ui.row('vieneu:Trúc Ly').querySelector('.voice-preview-button').textContent, 'Nghe thử');
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
  assert.match(ui.row('vieneu:Trúc Ly').querySelector('.voice-preview-button').textContent, /Dừng mẫu/);
  assert.equal(ui.row('vieneu:Trúc Ly').dataset.previewing, 'true');
  await ui.preview('vieneu:Trúc Ly');
  assert.equal(ui.el('voice-preview-audio').paused, true);
  assert.equal(ui.el('voice-preview-audio').src, undefined);
  assert.equal(ui.row('vieneu:Trúc Ly').dataset.previewing, 'false');
  assert.match(ui.row('vieneu:Trúc Ly').querySelector('.voice-preview-button').textContent, /Nghe thử/);
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

test('timeline uses current marker after init and replays READY colors', async () => {
  const ui = studio(); await ui.start();
  ui.el('video-player').currentTime = 5; await ui.el('video-player').emit('timeupdate');
  assert.equal(ui.el('playback-head-marker').style.left, '50%');
  assert.match(ui.el('slice-seg-0').className, /emerald/);
  assert.match(ui.el('seg-row-0').innerHTML, /&lt;b&gt;Xin chào/);
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
  assert.equal(ui.el('task-progress-value').textContent, '42%');
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
