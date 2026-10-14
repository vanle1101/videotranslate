import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

const source = readFileSync(new URL('../static/diagnostics.js', import.meta.url), 'utf8');
function ui() {
    const controls = Object.fromEntries(['log-search', 'log-level', 'log-display', 'log-live'].map(id => [id, {
        value: id === 'log-level' ? 'all' : id === 'log-display' ? 'readable' : '', checked: true,
        dataset: {}, listeners: {}, addEventListener(type, fn) {this.listeners[type] = fn;},
    }]));
    const events = [];
    const window = {dispatchEvent(event) {events.push(event.type);}};
    const document = {readyState: 'complete', getElementById: id => controls[id]};
    vm.runInNewContext(source, {window, document, CustomEvent: class {constructor(type) {this.type = type;}}});
    return {api: window.studioDiagnostics, controls, events};
}
const logs = [
    '2026-10-12 21:57:27 [INFO] [pipeline]: TTS_RAW_CACHE_HIT run_id=abc',
    '2026-10-12 21:57:35 [WARNING] [pipeline]: PACING_REJECTED reason=semantic_mismatch',
    '2026-10-12 21:57:36 [ERROR] [errors]: TRANSCRIPT_EDIT_FAILED segment_id=39 error_type=EdgeTTSRequestError',
    'Traceback (most recent call last):',
    '  File "test.py", line 7',
    'RuntimeError: provider <script>alert(1)</script>',
].join('\n');

test('readable summaries count events rather than traceback lines and preserve all evidence', () => {
    const {api} = ui();
    const html = api.render(logs, 'app');
    assert.match(html, /3\/3/);
    assert.equal((html.match(/<details/g) || []).length, 3);
    assert.match(html, /Dùng lại âm thanh đã tạo/);
    assert.match(html, /Dịch vụ giọng đọc chưa trả được âm thanh · Mã câu 39/);
    assert.match(html, /Traceback/);
    assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
    assert.doesNotMatch(html, /<script>/);
    assert.equal(api.filteredText(logs), logs);
});
test('search inside continuation keeps timestamp, header and complete traceback', () => {
    const {api, controls} = ui();
    controls['log-search'].value = 'test.py';
    const raw = api.filteredText(logs);
    assert.match(raw, /^2026-10-12 21:57:36/);
    assert.match(raw, /Traceback/);
    assert.match(raw, /RuntimeError/);
    assert.doesNotMatch(raw, /PACING_REJECTED/);
    assert.match(api.render(logs), /1\/3/);
});
test('search supports readable titles and level filter includes critical errors', () => {
    const {api, controls} = ui();
    controls['log-search'].value = 'dùng lại';
    assert.match(api.filteredText(logs), /TTS_RAW_CACHE_HIT/);
    controls['log-search'].value = '';
    controls['log-level'].value = 'ERROR';
    const critical = logs + '\n2026-10-12T21:58:00.123Z [CRITICAL] [app]: crash';
    const selected = api.filteredText(critical);
    assert.match(selected, /TRANSCRIPT_EDIT_FAILED/);
    assert.match(selected, /CRITICAL/);
    assert.doesNotMatch(selected, /WARNING/);
});
test('raw mode, unknown events and malformed prefixes stay escaped and readable', () => {
    const {api, controls} = ui();
    const raw = 'Unstructured start\n2026-10-12 21:57:35 [INFO] [<img>]: unknown <b>message</b>\ncontinued';
    const html = api.render(raw);
    assert.match(html, /Unstructured start/);
    assert.match(html, /unknown &lt;b&gt;message&lt;\/b&gt;/);
    assert.match(html, /&lt;img&gt;/);
    controls['log-display'].value = 'raw';
    assert.doesNotMatch(api.render(raw), /<details/);
    assert.equal(api.filteredText(raw), raw);
});
test('empty and no-match states distinguish absence from filtering', () => {
    const {api, controls} = ui();
    assert.match(api.render(''), /Chưa có nhật ký/);
    controls['log-search'].value = 'not-present';
    assert.match(api.render(logs), /Không có sự kiện khớp/);
    assert.equal(api.filteredText(logs), '');
});
test('filter events are emitted for all controls and live toggle participates in key', () => {
    const {api, controls, events} = ui();
    const original = api.filtersKey();
    for (const [id, type] of [['log-search', 'input'], ['log-level', 'change'], ['log-display', 'change'], ['log-live', 'change']]) {
        controls[id].listeners[type]();
    }
    assert.deepEqual(events, Array(4).fill('studio:log-filter'));
    controls['log-live'].checked = false;
    assert.notEqual(api.filtersKey(), original);
});
test('timeout and speech budget errors identify their actual cause over generic failed events', () => {
    const {api} = ui();
    assert.match(api.render('2026-10-12 21:57:35 [ERROR] [errors]: TRANSCRIPT_EDIT_FAILED error_type=SpeechBudgetError'), /Lời đọc vượt thời lượng/);
    assert.match(api.render('2026-10-12 21:57:35 [ERROR] [errors]: REVIEW_FAILED error_type=OpenCodeTimeoutError'), /quá thời gian chờ/);
});
