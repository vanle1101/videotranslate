/** Attach only after the user enables Chrome's remote-debugging connection. */
export function installChromeConnection(driver, chromium, mode) {
  if (mode !== 'existing') return;

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
    const old = owned;
    owned = null;
    await release(old);
    if (version !== generation) throw connectionError();
    const record = { browser: null, ctx: null, page: null, disconnected: false };
    owned = record;
    const stillCurrent = () => {
      if (version !== generation || owned !== record) throw connectionError();
    };
    try {
      record.browser = await chromium.connectOverCDP('chrome', {
        noDefaults: true,
        timeout: 20000,
      });
      stillCurrent();
      record.ctx = record.browser.contexts()[0];
      if (!record.ctx) throw connectionError();
      // Never inspect existing tabs, cookies, storage, or another Chrome profile.
      record.page = await record.ctx.newPage();
      stillCurrent();
      record.page.setDefaultTimeout(20000);
      await record.page.goto('https://muse.ai/', {
        waitUntil: 'domcontentloaded',
        timeout: 60000,
      });
      stillCurrent();
      await record.page.bringToFront();
      stillCurrent();
      driver.browser = record.browser;
      driver.ctx = record.ctx;
      driver.page = record.page;
      return record.page;
    } catch {
      if (owned === record) owned = null;
      await release(record);
      // Do not expose endpoint details, browser diagnostics, or profile paths.
      throw connectionError();
    }
  }

  driver.launch = async function () {
    if (closing) await closing;
    if (pending) return pending;
    if (owned?.page && !owned.page.isClosed() && owned.browser.isConnected()) {
      try {
        await owned.page.bringToFront();
        return owned.page;
      } catch {
        throw connectionError();
      }
    }
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
