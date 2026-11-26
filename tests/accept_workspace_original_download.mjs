import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createReadStream} from 'node:fs';
import {readFile, stat, writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const require = createRequire(import.meta.url);
const {chromium} = require(path.join(process.env.HOME, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const {RH_ORIGINAL_BASE, RH_ORIGINAL_DEMO, RH_ORIGINAL_CASE, RH_ORIGINAL_RECEIPT} = process.env;
const base = new URL(RH_ORIGINAL_BASE);
const fixture = JSON.parse(await readFile(RH_ORIGINAL_CASE, 'utf8'));
const record = {status: 'failed', checks: [], screens: [], layouts: [], errors: [], externalRequests: [], browser_closed: false};
let browser;

async function workspace(online, check) {
  const context = await browser.newContext({acceptDownloads: true, viewport: {width: 1440, height: 900}, reducedMotion: 'reduce'});
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.protocol === 'data:' || (online ? url.origin === base.origin : url.protocol === 'file:')) return route.continue();
    record.externalRequests.push(url.origin);
    return route.abort();
  });
  try {
    const page = await context.newPage();
    page.on('pageerror', error => record.errors.push(error.message));
    if (online) {
      const {token} = await (await page.request.get(new URL('/api/auth/status', base).href)).json();
      assert.ok((await page.request.post(new URL('/api/auth/login', base).href, {
        headers: {'X-ResearchHelper-Token': token}, data: {account: 'admin', password: '66666666', remember: false},
      })).ok());
    }
    await page.goto(online ? base.href : pathToFileURL(path.join(RH_ORIGINAL_DEMO, 'index.html')).href);
    await page.locator('.workspace-shell[data-initializing="false"]').waitFor();
    await check(page);
  } finally { await context.close(); }
}

async function selectTask(page, id) {
  await page.locator('.task-item[data-task="' + id + '"]').click();
  await page.waitForFunction(current => selected?.id === current, id);
  const desk = page.locator('[data-mode-switch] [data-mode="desk"]');
  if (await desk.getAttribute('aria-pressed') !== 'true') await desk.click();
  await page.locator('#view-1').click();
}

async function download(page, button, expected) {
  const pending = page.waitForEvent('download');
  await button.focus();
  await page.keyboard.press('Enter');
  const file = await pending;
  const saved = await file.path();
  const digest = createHash('sha256');
  for await (const chunk of createReadStream(saved)) digest.update(chunk);
  assert.equal(file.suggestedFilename(), expected.name);
  assert.equal((await stat(saved)).size, expected.byte_count);
  assert.equal(digest.digest('hex'), expected.sha256);
}

async function showSource(page, id) {
  const target = page.locator('#content button[data-source="' + id + '"]').first();
  const library = target.locator('xpath=ancestor::details[1]');
  if (await library.count() && await library.getAttribute('open') === null) await library.locator('summary').first().click();
  await target.click();
  await page.locator('#dialog[open] #source-reader').waitFor();
}

try {
  browser = await chromium.launch({channel: 'chrome', headless: true, args: ['--disable-gpu']});
  await workspace(true, async page => {
    await selectTask(page, fixture.task);
    await showSource(page, fixture.artifact);
    const large = fixture.files['有效原件.docx'];
    await download(page, page.locator('#source-reader [data-original-file="' + large.id + '"]'), large);
    record.checks.push('source reader saves the 24MiB original with its exact SHA-256');
    await page.keyboard.press('Escape');
    await page.locator('.source-process>summary').click();
    const failed = fixture.files['解析失败.txt'];
    const pending = page.locator('.original-recovery-row').filter({hasText: failed.name});
    assert.equal(await pending.locator('[data-retry-original]').count(), 1);
    await download(page, pending.locator('[data-original-file]'), failed);
    record.checks.push('failed parsing preserves a direct download with BOM and non-UTF8 bytes');
    await page.locator('#content [data-action="original-files"]').click();
    const empty = fixture.files['空原件.txt'];
    await download(page, page.locator('#dialog [data-original-file="' + empty.id + '"]'), empty);
    record.checks.push('retained file library preserves a zero-byte original');
    await page.keyboard.press('Escape');

    await selectTask(page, fixture.history);
    const open = page.locator('#content [data-action="original-files"]');
    await open.click();
    const rows = page.locator('#dialog [data-original-file]');
    assert.equal(await rows.count(), 2);
    for (const [width, height] of [[1440, 900], [1024, 768], [390, 844]]) {
      await page.setViewportSize({width, height});
      assert.equal(await rows.last().isVisible(), true);
      assert.equal(await page.locator('#dialog').evaluate(dialog => dialog.scrollWidth > dialog.clientWidth + 1), false);
      record.layouts.push({width, height});
    }
    for (const file of fixture.versions) {
      const row = page.locator('#dialog [data-original-file="' + file.id + '"]');
      assert.match(await row.innerText(), new RegExp('研究版本' + file.research_version));
      await download(page, row, file);
    }
    record.checks.push('same-named originals from two versions retain distinct exact bytes');
    await page.keyboard.press('Escape');
    assert.ok(await open.evaluate(button => button === document.activeElement));

    const previous = fixture.versions[0];
    const route = '**/original-file/' + previous.research_version + '/' + previous.id;
    let downloads = 0;
    const observe = () => { downloads++; };
    page.on('download', observe);
    await page.route(route, request => request.fulfill({contentType: 'application/octet-stream',
      body: Buffer.alloc(previous.byte_count, 1)}));
    await open.click();
    const target = page.locator('#dialog [data-original-file="' + previous.id + '"]');
    await target.click();
    await page.locator('#dialog [data-original-feedback]:visible').filter({hasText: '指纹校验失败'}).waitFor();
    assert.equal(downloads, 0);
    await page.unroute(route);
    record.checks.push('incorrect bytes are rejected before opening a download');

    let started, release;
    const requested = new Promise(resolve => { started = resolve; });
    const barrier = new Promise(resolve => { release = resolve; });
    await page.route(route, async request => {
      started();
      await barrier;
      await request.continue();
    });
    const response = page.waitForResponse(result => result.url().endsWith('/' + previous.id));
    await target.click();
    await requested;
    await page.keyboard.press('Escape');
    release();
    await response;
    await page.waitForFunction(() => downloadingOriginals.size === 0);
    assert.equal(downloads, 0);
    page.off('download', observe);
    record.checks.push('closing the file dialog prevents a late response from saving');
  });
  await workspace(false, async page => {
    const raw = Buffer.from([239,187,191,0,255]);
    const file = {name:'离线原件.txt', byte_count:raw.length, sha256:createHash('sha256').update(raw).digest('hex')};
    const id = await page.evaluate(({file, encoded}) => {
      const task = tasks.find(item => item.fixture === 'mechanism');
      const source = {name:file.name, hash:file.sha256, original_base64:encoded,
        status:'解析受限', parts:[]};
      task.materials = [{...source,id:'fixture-original',task_id:task.id,research_version:task.version,
        kind:'material',snapshot:source}];
      return task.id;
    }, {file, encoded:raw.toString('base64')});
    await selectTask(page, id);
    await page.locator('#content [data-action="original-files"]').click();
    await download(page, page.locator('#dialog [data-original-file="fixture-original"]'), file);
    record.checks.push('standalone demo can download a retained local original offline');
  });
  assert.deepEqual(record.errors, []);
  assert.deepEqual(record.externalRequests, []);
  record.status = 'passed';
} catch (error) {
  record.error = error.message;
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  record.browser_closed = true;
  await writeFile(RH_ORIGINAL_RECEIPT, JSON.stringify(record, null, 2));
  console.log(JSON.stringify(record));
}
