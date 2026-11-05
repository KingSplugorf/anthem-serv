// Run after `cargo build -p bigs` with playwright-core on NODE_PATH and Chromium
// installed. Starts an isolated BIGS instance; never opens a real pilot save.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const { chromium } = require('playwright-core');

(async () => {
  const root = path.resolve(__dirname, '../../..');
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'anthem-workbench-'));
  const probe = net.createServer();
  probe.listen(0, '127.0.0.1');
  await once(probe, 'listening');
  const port = probe.address().port;
  await new Promise((resolve) => probe.close(resolve));
  const state = path.join(directory, 'state.json');
  await fs.writeFile(state, JSON.stringify({
    niveau_pilote: 1,
    pilotes: [{ id: 1, name: '<img src=x onerror="window.injected=true">', gender: 'Male', appearance: '' }],
    progression: { '1': { schema: 1, xp: 1000, materials: { parts: 10, ember_1: 3 } } },
  }));
  const binary = process.env.BIGS_TEST_BINARY || path.join(root, 'target/debug', process.platform === 'win32' ? 'bigs.exe' : 'bigs');
  const child = spawn(binary, [], {
    cwd: root,
    env: { ...process.env, BIGS_PORT: String(port), LISTEN_HOST: '127.0.0.1',
      BIGS_ETAT: state, BIGS_SAUVEGARDES: path.join(directory, 'commits'), BIGS_PILOTES_SAUVEGARDES: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let browser;
  try {
    const ready = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('BIGS readiness timed out')), 10000);
      child.stdout.on('data', (chunk) => {
        if (chunk.toString().includes('bigs sur')) { clearTimeout(timer); resolve(); }
      });
      child.once('error', (error) => { clearTimeout(timer); reject(error); });
      child.once('exit', (code) => { clearTimeout(timer); reject(Error(`BIGS exited ${code}`)); });
    });
    // Drain logs without including pilot or environment details in test output.
    child.stderr.on('data', () => {});
    await ready;
    browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true,
      args: ['--no-sandbox', '--disable-dev-shm-usage'] });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const url = `http://127.0.0.1:${port}`;
    await page.goto(url + '/progression');
    await page.getByRole('status').filter({ hasText: 'Ready.' }).waitFor();
    assert.equal(await page.locator('#level').textContent(), 'Level 2');
    assert.equal(await page.evaluate(() => window.injected), undefined);
    assert.match(await page.locator('#pilot').textContent(), /<img/);

    // Deliver a craft to BIGS and then lose its response. Retrying the button
    // must reuse the stored transaction ID rather than charge another recipe.
    await page.route('**/craft', async (route) => {
      const response = await route.fetch();
      assert.equal(response.status(), 200);
      await route.abort('failed');
    });
    await page.getByRole('button', { name: 'Craft', exact: true }).click();
    await page.waitForFunction(() => /fetch|network/i.test(document.getElementById('status').textContent));
    await page.unroute('**/craft');
    // Also exercise persistence of the pending ID across a page reload.
    await page.reload();
    await page.getByRole('status').filter({ hasText: 'Ready.' }).waitFor();
    await page.getByRole('button', { name: 'Craft', exact: true }).click();
    await page.getByRole('status').filter({ hasText: 'Saved.' }).waitFor();
    let result = await (await fetch(url + '/local/progression/characters/1')).json();
    assert.equal(result.items.length, 1);
    assert.equal(result.materials.parts, 0);
    assert.equal(await page.evaluate(() => sessionStorage.length), 0);
    await page.getByRole('button', { name: 'Salvage', exact: true }).click();
    await page.waitForFunction(() => document.getElementById('items').textContent === 'No earned equipment yet.');
    result = await (await fetch(url + '/local/progression/characters/1')).json();
    assert.equal(result.items.length, 0);
    assert.equal(result.materials.parts, 4);

    await page.route('**/local/progression', (route) => route.fulfill({ json: { pilots: [] } }));
    await page.reload();
    await page.getByRole('status').filter({ hasText: 'Create a pilot' }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Craft', exact: true }).isDisabled(), true);
    assert.deepEqual(errors, []);
    console.log('Workbench browser passed: rendering, safe pilot text, craft retry after lost response/reload, salvage, empty pilot state.');
  } finally {
    if (browser) await browser.close();
    if (child.exitCode === null) { const exited = once(child, 'exit'); child.kill(); await exited; }
    await fs.rm(directory, { recursive: true, force: true });
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
