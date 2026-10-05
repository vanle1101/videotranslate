import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installMuseChatAdapter, waitForRenderedReply } from '../integrations/muse/chat_adapter.mjs';

const selectors = { assistant: 'assistant', stopButton: 'stop', approvalStack: 'approval', errorNotice: 'error', message: 'message' };

function replyFixture(frames) {
  let elapsed = 0;
  const frame = () => frames[Math.min(Math.floor(elapsed / 100), frames.length - 1)];
  return {
    page: { locator: key => ({
      count: async () => key === 'assistant' ? 1 : Number(Boolean(frame()[key])),
      last: () => ({ innerText: async () => frame().text || '' }),
    }) },
    options: { timeoutMs: 1200, stableMs: 200, pollMs: 100, now: () => elapsed, pause: async ms => { elapsed += ms; } },
  };
}

test('empty response placeholders are not accepted before streaming starts', async () => {
  const { page, options } = replyFixture([
    {}, {}, {}, {}, { text: 'Xin', stop: true }, { text: 'Xin chào', stop: true },
    { text: 'Xin chào' }, { text: 'Xin chào' }, { text: 'Xin chào' },
  ]);
  assert.deepEqual(await waitForRenderedReply(page, selectors, options), { reply: 'Xin chào' });
});

test('response that remains empty reaches timeout instead of a false successful reply', async () => {
  const { page, options } = replyFixture([{}]);
  assert.deepEqual(await waitForRenderedReply(page, selectors, options), { reply: '', timedOut: true });
});

for (const [flag, expected] of [['approval', { reply: '', needsApproval: true }], ['error', { reply: '', error: 'browser_error' }]]) {
  test(`${flag} never returns partial text`, async () => {
    const { page, options } = replyFixture([{ text: 'partial', [flag]: true }]);
    assert.deepEqual(await waitForRenderedReply(page, selectors, options), expected);
  });
}

function chatFixture({ stableSelector = true, clickFails = false } = {}) {
  const calls = [];
  const button = { first() { return this; }, count: async () => Number(stableSelector), click: async () => { calls.push('click'); if (clickFails) throw Error('private data'); } };
  const page = {
    locator: key => key === selectors.message
      ? { first: () => ({ waitFor: async options => calls.push(['fresh', options.state]) }) }
      : (assert.equal(key, '[data-testid="hatch-chat-compose"]'), button),
    getByRole: (role, options) => { assert.equal(role, 'button'); assert.ok(options.name.test('Đoạn chat phụ mới')); return button; },
    url: () => 'https://muse.ai/',
  };
  const driver = {
    requirePage: async () => page, gotoApp: async () => {}, waitForComposer: async () => calls.push('composer'),
    _newChat: async () => assert.fail('upstream English-only fallback must not run'),
    _run: async () => ({ reply: 'Xin chào' }),
  };
  installMuseChatAdapter(driver, selectors);
  return { driver, calls };
}

for (const stableSelector of [true, false]) {
  test(`fresh chat works with ${stableSelector ? 'stable test id' : 'Vietnamese control'} and waits for old messages to disappear`, async () => {
    const { driver, calls } = chatFixture({ stableSelector });
    await driver._newChat();
    assert.deepEqual(calls, ['composer', 'click', 'composer', ['fresh', 'hidden']]);
  });
}

test('failure to open a fresh side chat does not fall back to the main conversation', async () => {
  const { driver } = chatFixture({ clickFails: true });
  await assert.rejects(driver._newChat(), /^Error: muse_new_chat_failed$/);
});

test('a complete upstream response remains unchanged', async () => {
  const { driver } = chatFixture();
  assert.deepEqual(await driver._run('synthetic'), { reply: 'Xin chào' });
});

test('empty upstream completion resumes waiting in the same fresh conversation', async t => {
  let elapsed = 0;
  t.mock.method(Date, 'now', () => { elapsed += 3000; return elapsed; });
  const { page } = replyFixture([{ text: 'Xin chào' }]);
  const driver = { page, _newChat() {}, _run: async () => ({ reply: '', elapsedMs: 3000 }) };
  installMuseChatAdapter(driver, selectors);
  const result = await driver._run('synthetic', { timeoutMs: 20000 });
  assert.equal(result.reply, 'Xin chào');
  assert.equal(result.timedOut, undefined);
});
