import { performance } from 'node:perf_hooks';

const CONNECTION_TIMEOUT_MS = 60000;

/** Attach only after the user enables Chrome's remote-debugging connection. */
export function installChromeConnection(driver, chromium, mode, port = 0) {
  if (mode !== 'existing') return;
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('chrome_connection_required');
  // Chrome's consent-based server uses this fixed WebSocket path. An explicit
  // local port targets the instance chosen by the user without profile discovery.
  const endpoint = port ? `ws://127.0.0.1:${port}/devtools/browser` : 'chrome';

  let owned = null;
  let pending = null;
  let closing = null;
  let generation = 0;
  const connectionError = () => new Error('chrome_connection_required');

  function clearDriver(record) {
    if (driver.browser === record?.browser) {
      driver.browser = null;
      driver.ctx = null;
      driver.page = null;
    }
  }

  async function release(record) {
    if (!record) return;
    clearDriver(record);
    // Only this adapter's newly created page belongs to the application.
    if (record.page && !record.page.isClosed()) {
      await record.page.close({ runBeforeUnload: false }).catch(() => {});
    }
    if (record.browser && !record.disconnected) {
      record.disconnected = true;
      // For connectOverCDP, Playwright 1.63 implements browser.close() by closing
      // the CDP transport. It does not send Browser.close to the user's Chrome.
      await record.browser.close().catch(() => {});
    }
  }

  async function connect(version) {
    let record = owned;
    const reusable = record?.page && !record.page.isClosed() && record.browser.isConnected();
    if (!reusable) {
      owned = null;
      await release(record);
      if (version !== generation) throw connectionError();
      record = { browser: null, ctx: null, page: null, disconnected: false, ready: false };
      owned = record;
    }
    let phase = 'connect';
    const connectionStarted = performance.now();
    const stillCurrent = () => {
      if (version !== generation || owned !== record) throw connectionError();
    };
    try {
      if (!reusable) {
        record.browser = await chromium.connectOverCDP(endpoint, {
          noDefaults: true,
          timeout: CONNECTION_TIMEOUT_MS,
        });
        stillCurrent();
        phase = 'page';
        record.ctx = record.browser.contexts()[0];
        if (!record.ctx) throw connectionError();
        // Never inspect existing tabs, cookies, storage, or another Chrome profile.
        record.page = await record.ctx.newPage();
        stillCurrent();
        record.page.setDefaultTimeout(20000);
      }
      phase = 'navigate';
      if (!record.ready) {
        await record.page.goto('https://muse.ai/', {
          waitUntil: 'domcontentloaded',
          timeout: 60000,
        });
      }
      stillCurrent();
      await record.page.bringToFront();
      stillCurrent();
      record.ready = true;
      driver.browser = record.browser;
      driver.ctx = record.ctx;
      driver.page = record.page;
      return record.page;
    } catch (error) {
      // Playwright's named-channel connector can wrap a transport TimeoutError
      // in a plain Error. Preserve the deadline diagnostic in that case too.
      const connectionTimedOut = phase === 'connect' &&
        (error?.name === 'TimeoutError' || performance.now() - connectionStarted >= CONNECTION_TIMEOUT_MS);
      // Keep the owned tab and consented connection after a recoverable load
      // failure, so retrying Muse does not create another tab or consent dialog.
      if (phase === 'navigate' && version === generation && owned === record &&
          record.page && !record.page.isClosed() && record.browser.isConnected()) {
        record.ready = false;
        throw new Error('muse_navigation_failed');
      }
      if (owned === record) owned = null;
      await release(record);
      // Do not expose endpoint details, browser diagnostics, or profile paths.
      if (version !== generation) throw connectionError();
      const code = phase === 'connect'
        ? (connectionTimedOut ? 'chrome_connection_timeout' : 'chrome_connection_required')
        : phase === 'page' ? 'chrome_page_failed' : 'muse_navigation_failed';
      throw new Error(code);
    }
  }

  driver.launch = async function () {
    if (closing) await closing;
    if (pending) return pending;
    const operation = connect(generation);
    pending = operation;
    try {
      return await operation;
    } finally {
      if (pending === operation) pending = null;
    }
  };

  driver.close = function () {
    if (closing) return closing;
    generation += 1;
    const record = owned;
    owned = null;
    const operation = (async () => {
      await release(record);
      // A connection or newPage may finish after close began. connect() checks
      // its generation at each boundary and releases any late-owned resources.
      await pending?.catch(() => {});
      await release(record);
    })();
    closing = operation.finally(() => {
      closing = null;
    });
    return closing;
  };
}
