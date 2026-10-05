// Minimal authenticated adapter for the pinned Muse driver. No MCP or public shim.
import http from 'node:http';
import { timingSafeEqual } from 'node:crypto';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { installMuseChatAdapter } from './chat_adapter.mjs';

const token = process.env.MUSE_BRIDGE_TOKEN;
if (!token || token.length < 32 || !process.env.MUSE_RUNTIME_DIR || !process.env.MUSE_PROFILE_DIR) process.exit(1);
delete process.env.MUSE_CDP;
process.env.MUSE_URL = 'https://muse.ai/';
process.env.MUSE_HEADLESS = '0';
const { driver, SELECTORS } = await import(pathToFileURL(path.join(process.env.MUSE_RUNTIME_DIR, 'muse-driver.mjs')).href);
installMuseChatAdapter(driver, SELECTORS);
const browserMode = process.env.MUSE_BROWSER_MODE || 'dedicated';
if (!['existing', 'dedicated'].includes(browserMode)) process.exit(1);
if (browserMode === 'existing') {
  const { installChromeConnection } = await import('./chrome_connection.mjs');
  const { chromium } = await import(pathToFileURL(path.join(process.env.MUSE_RUNTIME_DIR, 'node_modules/playwright-core/index.mjs')).href);
  installChromeConnection(driver, chromium, browserMode, Number(process.env.MUSE_CHROME_PORT || 0));
}
const send = (res, status, data) => {
  if (res.destroyed || res.writableEnded) return;
  res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(data));
};
async function health() {
  if (!driver.isRunning()) return { browser_running: false, logged_in: false, composer_ready: false };
  const auth = await driver.checkAuth();
  const visible = await driver.page.locator(SELECTORS.editor).first().isVisible().catch(() => false);
  return { browser_running: true, logged_in: auth.ok === true, composer_ready: visible };
}
async function readBody(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 512 * 1024) throw new Error('invalid_request');
    chunks.push(chunk);
  }
  const data = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('invalid_request');
  return data;
}
let closing = false;
async function close() {
  if (closing) return;
  closing = true;
  const deadline = setTimeout(() => process.exit(1), 6000);
  deadline.unref();
  await driver.close().catch(() => {});
  server.close(() => process.exit(0));
  server.closeAllConnections();
}
const server = http.createServer(async (req, res) => {
  const supplied = Buffer.from(req.headers.authorization || '');
  const expected = Buffer.from(`Bearer ${token}`);
  if (req.headers.origin || supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
    return send(res, 401, { error: 'unauthorized' });
  }
  const route = `${req.method} ${req.url}`;
  try {
    if (route === 'GET /health') return send(res, 200, await health());
    if (route === 'POST /shutdown') {
      send(res, 200, { ok: true });
      return void close();
    }
    if (route === 'POST /login') {
      await readBody(req);
      await driver.launch();
      return send(res, 200, await health());
    }
    if (route !== 'POST /chat') return send(res, 404, { error: 'invalid_request' });
    const body = await readBody(req);
    if (Object.keys(body).some(k => !['prompt', 'system'].includes(k)) ||
        typeof body.prompt !== 'string' || !body.prompt.trim() ||
        (body.system !== undefined && typeof body.system !== 'string')) {
      return send(res, 400, { error: 'invalid_request' });
    }
    const state = await health();
    if (!state.logged_in) return send(res, 409, { error: 'login_required' });
    const prompt = [body.system, body.prompt].filter(Boolean).join('\n\n');
    const result = await driver.chat(prompt, { newThread: true, timeoutMs: 240000 });
    // Partial text must never be accepted as a completed subtitle translation.
    if (result.needsApproval) return send(res, 409, { error: 'approval_required' });
    if (result.timedOut) return send(res, 504, { error: 'timed_out' });
    if (result.error) return send(res, 502, { error: 'browser_error' });
    if (typeof result.reply !== 'string' || !result.reply.trim()) return send(res, 502, { error: 'empty_reply' });
    send(res, 200, { reply: result.reply.trim() });
  } catch (err) {
    if (['chrome_connection_required', 'chrome_connection_timeout', 'chrome_page_failed', 'muse_navigation_failed', 'muse_new_chat_failed'].includes(err.message)) {
      return send(res, 409, { error: err.message });
    }
    send(res, err instanceof SyntaxError || err.message === 'invalid_request' ? 400 : 502,
      { error: err instanceof SyntaxError || err.message === 'invalid_request' ? 'invalid_request' : 'browser_error' });
  }
});
server.requestTimeout = 300000;
server.headersTimeout = 15000;
server.listen(0, '127.0.0.1', () => process.stdout.write(JSON.stringify({ port: server.address().port }) + '\n'));
process.on('SIGINT', close);
process.on('SIGTERM', close);
process.stdin.resume();
process.stdin.on('end', close);
