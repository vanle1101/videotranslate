// Local compatibility fixes for the pinned text-chat driver.
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

export async function waitForRenderedReply(page, selectors, {
  timeoutMs, stableMs = 2500, pollMs = 300, now = Date.now, pause = sleep,
}) {
  const deadline = now() + timeoutMs;
  let text = '';
  let stableSince = now();
  while (now() < deadline) {
    if (await page.locator(selectors.approvalStack).count()) return { reply: '', needsApproval: true };
    if (await page.locator(selectors.errorNotice).count()) return { reply: '', error: 'browser_error' };
    const assistant = page.locator(selectors.assistant);
    const next = await assistant.count() ? (await assistant.last().innerText()).trim() : '';
    if (next !== text) {
      text = next;
      stableSince = now();
    }
    const streaming = await page.locator(selectors.stopButton).count();
    // Muse mounts an empty response before the model starts streaming. An empty
    // placeholder, however long it lasts, cannot represent a completed answer.
    if (text && !streaming && now() - stableSince >= stableMs) return { reply: text };
    await pause(pollMs);
  }
  return { reply: '', timedOut: true };
}

export function installMuseChatAdapter(driver, selectors) {
  // The adapter applies to the pinned browser driver, not other service drivers.
  if (typeof driver._run !== 'function' || typeof driver._newChat !== 'function') return;
  const run = driver._run;
  driver._newChat = async function () {
    const page = await this.requirePage();
    await this.gotoApp();
    await this.waitForComposer();
    const byTestId = page.locator('[data-testid="hatch-chat-compose"]');
    const localized = page.getByRole('button', { name: /^(new side chat|đoạn chat phụ mới)$/i });
    const button = await byTestId.count() ? byTestId.first() : localized.first();
    try {
      await button.click({ timeout: 10000 });
      await this.waitForComposer();
      // Wait for the fresh side chat to replace the main conversation before
      // recording message counts or filling its composer.
      await page.locator(selectors.message).first().waitFor({ state: 'hidden', timeout: 10000 });
    } catch {
      throw new Error('muse_new_chat_failed');
    }
    return { ok: true, url: page.url() };
  };
  driver._run = async function (prompt, options = {}) {
    const started = Date.now();
    const timeoutMs = options.timeoutMs ?? 240000;
    const result = await run.call(this, prompt, options);
    if (result.reply?.trim() || result.timedOut || result.error || result.needsApproval) return result;
    const remaining = Math.max(0, timeoutMs - (Date.now() - started));
    const completed = await waitForRenderedReply(this.page, selectors, { timeoutMs: remaining });
    return { ...result, ...completed, elapsedMs: Date.now() - started };
  };
}
