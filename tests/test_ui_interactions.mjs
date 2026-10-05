import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

const source = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
const template = readFileSync(new URL('../templates/index.html', import.meta.url), 'utf8');

function studio() {
  const elements = new Map(), audio = [], sockets = [], requests = [], alerts = [];
  let ready;
  class Element {
    constructor(id = '') {
      this.id = id; this.events = {}; this.children = []; this.style = {}; this.dataset = {};
      this.value = ''; this.files = []; this.checked = true;
      this.paused = true; this.currentTime = 0; this.playbackRate = 1; this.ended = false;
      this.playCount = 0; this.classes = new Set();
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
    appendChild(child) { this.children.push(child); if (child.id) elements.set(child.id, child); }
    replaceChildren(...children) { this.children = children; this.options = children; }
    add(option) { this.options.push(option); }
    setAttribute(name, value) { this[name] = value; }
    async click() { await this.emit('click'); }
    focus() { document.activeElement = this; }
    load() { this.currentTime = 0; }
    pause() { this.paused = true; this.emit('pause'); }
    async play() { this.paused = false; this.playCount++; await this.emit('play'); }
    getBoundingClientRect() { return { left: 0, width: 100 }; }
    querySelectorAll() { return []; }
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
    createElement: () => new Element(), querySelectorAll: () => [],
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
  ]);
  const window = { location: { protocol: 'http:', host: 'localhost' } };
  const fetch = async (url, options = {}) => {
    requests.push({ url, options });
    const reply = replies.get(url) || {};
    if (typeof reply === 'function') return reply(options);
    return { ok: !reply.failure, json: async () => reply };
  };
  vm.runInNewContext(source, {
    document, window, Audio, WebSocket, fetch, console, alert: value => alerts.push(value),
    Option: function(text, value) { this.text = text; this.value = value; },
    URL: { createObjectURL: () => 'blob:video', revokeObjectURL() {} },
    localStorage: { removeItem() {} }, setTimeout() {}, setInterval() { return 1; }, clearInterval() {},
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
  return { el, audio, sockets, requests, replies, alerts, window, flush, start };
}

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
