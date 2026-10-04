import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installChromeConnection } from '../integrations/muse/chrome_connection.mjs';

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const tick = () => new Promise(resolve => setImmediate(resolve));

function fixture({ connectGate, pageGate, navigateGate, failConnect, failNavigate, emptyContexts } = {}) {
  const calls = [];
  const pages = [];
  const browsers = [];
  const driver = {
    browser: null, ctx: null, page: null,
    launch: () => { throw Error('dedicated browser must not launch'); },
    close: () => { throw Error('dedicated browser must not close'); },
    isRunning() { return !!(this.ctx && this.page && !this.page.isClosed()); },
  };
  const chromium = {
    launchPersistentContext() { assert.fail('must not launch a separate browser'); },
    async connectOverCDP(endpoint, options) {
      calls.push(['connect', endpoint, options]);
      if (failConnect) throw Error('private-browser-profile-secret');
      if (connectGate) await connectGate.promise;
      let connected = true;
      const context = {
        pages() { assert.fail('must not enumerate existing tabs'); },
        cookies() { assert.fail('must not read cookies'); },
        close() { assert.fail('must not close the shared context'); },
        async newPage() {
          calls.push(['newPage']);
          if (pageGate) await pageGate.promise;
          let closed = false;
          const page = {
            isClosed: () => closed,
            setDefaultTimeout(value) { calls.push(['timeout', value]); },
            async goto(url, options) {
              calls.push(['goto', url, options]);
              if (failNavigate) throw Error('private-navigation-secret');
              if (navigateGate) await navigateGate.promise;
            },
            async bringToFront() { calls.push(['front']); },
            async close(options) {
              calls.push(['pageClose', options]);
              closed = true;
            },
            userClose() { closed = true; },
          };
          pages.push(page);
          return page;
        },
      };
      const browser = {
        contexts: () => emptyContexts ? [] : [context],
        isConnected: () => connected,
        async close() { calls.push(['disconnect']); connected = false; },
      };
      browsers.push(browser);
      return browser;
    },
  };
  installChromeConnection(driver, chromium, 'existing');
  return { driver, calls, pages, browsers };
}

test('uses consent-based Chrome connection and only its own Muse tab', async () => {
  const { driver, calls, pages } = fixture();
  const page = await driver.launch();
  assert.equal(page, pages[0]);
  assert.equal(driver.page, page);
  assert.equal(driver.isRunning(), true);
  assert.deepEqual(calls, [
    ['connect', 'chrome', { noDefaults: true, timeout: 20000 }],
    ['newPage'],
    ['timeout', 20000],
    ['goto', 'https://muse.ai/', { waitUntil: 'domcontentloaded', timeout: 60000 }],
    ['front'],
  ]);
  await driver.close();
  assert.deepEqual(calls.slice(-2), [['pageClose', { runBeforeUnload: false }], ['disconnect']]);
  assert.deepEqual([driver.page, driver.ctx, driver.browser], [null, null, null]);
  await driver.close();
  assert.equal(calls.filter(call => call[0] === 'disconnect').length, 1);
});

test('reuses its own open page and brings it forward', async () => {
  const { driver, calls } = fixture();
  const first = await driver.launch();
  assert.equal(await driver.launch(), first);
  assert.equal(calls.filter(call => call[0] === 'connect').length, 1);
  assert.equal(calls.filter(call => call[0] === 'front').length, 2);
  await driver.close();
});

test('concurrent launches share one connection and one new tab', async () => {
  const connectGate = deferred();
  const { driver, calls } = fixture({ connectGate });
  const first = driver.launch();
  const second = driver.launch();
  await tick();
  assert.equal(calls.filter(call => call[0] === 'connect').length, 1);
  connectGate.resolve();
  assert.equal(await first, await second);
  assert.equal(calls.filter(call => call[0] === 'newPage').length, 1);
  await driver.close();
});

test('user closing the owned tab disconnects its old handle before reconnecting', async () => {
  const { driver, calls } = fixture();
  const first = await driver.launch();
  first.userClose();
  const second = await driver.launch();
  assert.notEqual(first, second);
  const reconnect = calls.findLastIndex(call => call[0] === 'connect');
  assert.equal(calls[reconnect - 1][0], 'disconnect');
  assert.equal(calls.filter(call => call[0] === 'pageClose').length, 0);
  await driver.close();
});

test('close allows a fresh connection on later launch', async () => {
  const { driver, calls } = fixture();
  const first = await driver.launch();
  await driver.close();
  assert.notEqual(await driver.launch(), first);
  assert.equal(calls.filter(call => call[0] === 'connect').length, 2);
  await driver.close();
});

for (const stage of ['connect', 'page', 'navigate']) {
  test(`shutdown during ${stage} releases late resources and leaves driver stopped`, async () => {
    const gate = deferred();
    const { driver, calls, pages, browsers } = fixture({ [`${stage}Gate`]: gate });
    const launching = driver.launch();
    const rejected = assert.rejects(launching, /^Error: chrome_connection_required$/);
    await tick();
    const closing = driver.close();
    gate.resolve();
    await Promise.all([closing, rejected]);
    assert.equal(driver.isRunning(), false);
    assert.deepEqual([driver.page, driver.ctx, driver.browser], [null, null, null]);
    assert.ok(pages.every(page => page.isClosed()));
    assert.ok(browsers.every(browser => !browser.isConnected()));
    assert.equal(calls.filter(call => call[0] === 'disconnect').length, 1);
  });
}

test('launch requested while closing waits before starting its fresh connection', async () => {
  const gate = deferred();
  const { driver, calls } = fixture({ connectGate: gate });
  const interrupted = assert.rejects(driver.launch(), /^Error: chrome_connection_required$/);
  await tick();
  const closing = driver.close();
  const replacement = driver.launch();
  gate.resolve();
  await Promise.all([closing, interrupted]);
  assert.equal(await replacement, driver.page);
  assert.equal(driver.isRunning(), true);
  assert.equal(calls.filter(call => call[0] === 'connect').length, 2);
  await driver.close();
});

for (const failure of ['failConnect', 'failNavigate', 'emptyContexts']) {
  test(`${failure} returns a fixed error and cleans up owned resources`, async () => {
    const { driver, pages, browsers } = fixture({ [failure]: true });
    await assert.rejects(driver.launch(), /^Error: chrome_connection_required$/);
    assert.equal(driver.isRunning(), false);
    assert.ok(pages.every(page => page.isClosed()));
    assert.ok(browsers.every(browser => !browser.isConnected()));
    await driver.close();
  });
}

test('dedicated mode leaves the upstream driver unchanged', () => {
  const launch = () => {};
  const close = () => {};
  const driver = { launch, close };
  installChromeConnection(driver, { connectOverCDP: () => assert.fail() }, 'dedicated');
  assert.equal(driver.launch, launch);
  assert.equal(driver.close, close);
});
