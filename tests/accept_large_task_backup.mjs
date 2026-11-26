import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createReadStream} from 'node:fs';
import {readFile, stat, writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const require = createRequire(import.meta.url);
const {chromium} = require(path.join(process.env.HOME, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const {RH_PACKAGE_BASE, RH_PACKAGE_DEMO, RH_PACKAGE_CASE, RH_PACKAGE_RECEIPT, RH_PACKAGE_FILE} = process.env;
const base = new URL(RH_PACKAGE_BASE);
const fixture = JSON.parse(await readFile(RH_PACKAGE_CASE, 'utf8'));
const record = {status: 'failed', checks: [], screens: [], layouts: [], errors: [], externalRequests: [], browser_closed: false};
let browser;

async function sha256(filename) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(filename)) hash.update(chunk);
  return hash.digest('hex');
}

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
      const status = await page.request.get(new URL('/api/auth/status', base).href);
      const {token} = await status.json();
      assert.ok((await page.request.post(new URL('/api/auth/login', base).href, {
        headers: {'X-ResearchHelper-Token': token}, data: {account: 'admin', password: '66666666', remember: false},
      })).ok());
    }
    await page.goto(online ? base.href : pathToFileURL(path.join(RH_PACKAGE_DEMO, 'index.html')).href);
    await page.locator('.workspace-shell[data-initializing="false"]').waitFor();
    await check(page);
  } finally { await context.close(); }
}

async function saveFrom(page, button) {
  const pending = page.waitForEvent('download', {timeout: 120000});
  await button.click();
  return pending;
}

try {
  browser = await chromium.launch({channel: 'chrome', headless: true, args: ['--disable-gpu']});
  await workspace(true, async page => {
    const task = page.locator('.task-item[data-task="' + fixture.task_id + '"]');
    await task.click();
    await page.waitForFunction(id => selected?.id === id, fixture.task_id);
    await page.locator('[data-mode-switch] [data-mode="desk"]').click();
    await page.locator('#view-3').click();
    await page.locator('#content [data-work-menu-toggle]').click();
    const downloaded = await saveFrom(page, page.locator('#report-more-menu [data-action="backup"]'));
    assert.equal(downloaded.suggestedFilename(), 'ResearchHelper任务备份.zip');
    await downloaded.saveAs(RH_PACKAGE_FILE);
    const bytes = (await stat(RH_PACKAGE_FILE)).size;
    const hash = await sha256(RH_PACKAGE_FILE);
    assert.ok(bytes > 45 * 1024 * 1024 && bytes < fixture.original_bytes + 2 * 1024 * 1024);
    record.checks.push('large app task automatically downloads a compact ZIP');

    const persisted = page.waitForResponse(response => response.url().endsWith('/api/workspace-draft') &&
      JSON.parse(response.request().postData() || '{}').state?.activeTaskId?.startsWith('rh_') &&
      JSON.parse(response.request().postData() || '{}').state.activeTaskId !== fixture.task_id && response.ok());
    await page.locator('#backup-file').setInputFiles(RH_PACKAGE_FILE);
    await page.waitForFunction(() => selected?.parent === 'legacy-import' && selected.backup_package?.files?.length === 3);
    const archiveId = await page.evaluate(() => selected.id);
    await persisted;
    await page.reload();
    await page.waitForFunction(id => selected?.id === id, archiveId);
    record.checks.push('ZIP import remains read-only and is selected again after reload');
    const archive = page.locator('[data-action="archive-files"]:visible');
    await archive.click();
    const rows = page.locator('#dialog[open] [data-action="archive-file"]');
    await rows.first().waitFor();
    assert.equal(await rows.count(), 3);
    for (const [width, height] of [[1440, 900], [1024, 768], [390, 844]]) {
      await page.setViewportSize({width, height});
      assert.ok(await rows.last().isVisible());
      assert.equal(await page.locator('#dialog').evaluate(dialog => dialog.scrollWidth > dialog.clientWidth + 1), false);
      record.layouts.push({width, height});
    }
    for (const [index, item] of [fixture.report, ...fixture.original_files].entries()) {
      const download = await saveFrom(page, rows.nth(index));
      const pathname = await download.path();
      if (item.filename) assert.equal(download.suggestedFilename(), item.filename);
      assert.equal((await stat(pathname)).size, item.byte_count);
      assert.equal(await sha256(pathname), item.sha256);
    }
    record.checks.push('frozen report and both original documents download byte for byte');
    const again = await saveFrom(page, page.locator('#dialog[open] [data-action="backup"]'));
    assert.equal(again.suggestedFilename(), 'ResearchHelper任务备份.zip');
    assert.equal(await sha256(await again.path()), hash);
    record.checks.push('redownload returns the original ZIP bytes');
    record.archiveId = archiveId;
    record.packageBytes = bytes;
    record.sha256 = hash;
  });
  await workspace(false, async page => {
    await page.locator('#backup-file').setInputFiles(RH_PACKAGE_FILE);
    await page.locator('#notice').filter({hasText: '请在ResearchHelper App导入ZIP备份'}).waitFor();
    assert.equal(await page.evaluate(() => tasks.some(task => task.parent === 'legacy-import')), false);
    record.checks.push('standalone file demo directs large ZIP imports to the app without a partial archive');
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
  await writeFile(RH_PACKAGE_RECEIPT, JSON.stringify(record, null, 2));
  console.log(JSON.stringify(record));
}
