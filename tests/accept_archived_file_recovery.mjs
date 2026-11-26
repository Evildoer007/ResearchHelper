import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile, writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const require = createRequire(import.meta.url);
const {chromium} = require(path.join(process.env.HOME, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const {RH_ARCHIVE_BASE, RH_ARCHIVE_DEMO, RH_ARCHIVE_RECEIPT, RH_ARCHIVE_COMPAT, RH_ARCHIVE_RETRY} = process.env;
const compatibility = RH_ARCHIVE_COMPAT ? JSON.parse(await readFile(RH_ARCHIVE_COMPAT, 'utf8')) : null;
const record = {status: 'failed', checks: [], screens: [], layouts: [], errors: [], externalRequests: [], browser_closed: false};
const sha256 = value => createHash('sha256').update(value).digest('hex');
const base = new URL(RH_ARCHIVE_BASE);
const navigation = '.workspace-shell[data-initializing="false"]';
const archiveDialog = '#dialog[open] #archive-files:not([aria-busy="true"])';
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
      const response = await page.request.get(new URL('/api/auth/status', base).href);
      const {token} = await response.json();
      const login = await page.request.post(new URL('/api/auth/login', base).href, {
        headers: {'X-ResearchHelper-Token': token},
        data: {account: 'admin', password: '66666666', remember: false},
      });
      assert.ok(login.ok());
    }
    await page.goto(online ? base.href : pathToFileURL(path.join(RH_ARCHIVE_DEMO, 'index.html')).href);
    await page.locator(navigation).waitFor();
    await check(page);
  } finally {
    await context.close();
  }
}

async function importBackup(page, backup) {
  const prior = await page.evaluate(() => selected?.id ?? null);
  await page.locator('#backup-file').setInputFiles({
    name: 'research-backup.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(backup)),
  });
  await page.waitForFunction(previous => selected?.parent === 'legacy-import' && selected.id !== previous, prior);
}

async function download(page, button) {
  const ready = page.waitForEvent('download', {timeout: 10000});
  await button.focus();
  await page.keyboard.press('Enter');
  const result = await ready;
  return {filename: result.suggestedFilename(), bytes: await readFile(await result.path())};
}

async function verifyFiles(page, backup, surface) {
  const entry = page.locator('[data-action="archive-files"]:visible');
  await entry.click();
  const panel = page.locator(archiveDialog);
  await panel.waitFor();
  const rows = panel.locator('[data-action="archive-file"]');
  assert.equal(await rows.count(), 3);
  assert.equal(await panel.locator('iframe, script, object, embed').count(), 0);
  assert.ok(await rows.nth(0).getAttribute('aria-label'));
  for (const [width, height] of [[1440, 900], [1024, 768], [390, 844]]) {
    await page.setViewportSize({width, height});
    assert.equal(await rows.nth(2).isVisible(), true);
    const overflow = await page.locator('#dialog').evaluate(dialog => dialog.scrollWidth > dialog.clientWidth + 1);
    assert.equal(overflow, false);
    record.layouts.push({surface, width, height});
  }
  for (const [index, report] of backup.reports.entries()) {
    const saved = await download(page, rows.nth(index));
    assert.match(saved.filename, new RegExp('_v' + report.version + '\\.html$'));
    assert.equal(sha256(saved.bytes), report.html_sha256);
  }
  record.checks.push(surface + ' two frozen editions download with their exact hashes');
  const original = await download(page, rows.nth(2));
  assert.equal(original.filename, backup.originals[0].name);
  assert.equal(sha256(original.bytes), backup.originals[0].sha256);
  record.checks.push(surface + ' original file downloads with its exact hash');
  await page.keyboard.press('Escape');
  await page.locator('#dialog').waitFor({state: 'hidden'});
  assert.ok(await entry.evaluate(button => button === document.activeElement));
}

try {
  browser = await chromium.launch({channel: 'chrome', headless: true, args: ['--disable-gpu']});
  let exported;
  await workspace(true, async page => {
    const archiveId = await page.evaluate(other => tasks.find(task => task.parent === 'legacy-import' && task.id !== other).id,
      compatibility?.archive_id ?? null);
    await page.locator('.task-item[data-task="' + archiveId + '"]').click();
    await page.locator('[data-action="archive-files"]:visible').waitFor();
    const response = await page.request.get(new URL('/api/tasks/' + archiveId + '/backup', base).href);
    assert.ok(response.ok());
    exported = JSON.parse(await response.text());
    assert.equal(exported.reports.length, 2);
    assert.ok(exported.reports.every(report => sha256(report.html) === report.html_sha256));
    await verifyFiles(page, exported, 'app');
    await page.setViewportSize({width: 1440, height: 900});
    await page.locator('[data-mode-switch] [data-mode="desk"]').click();
    let reading, release;
    const pending = new Promise(resolve => { reading = resolve; });
    const delay = new Promise(resolve => { release = resolve; });
    const route = '**/api/tasks/' + archiveId + '/backup';
    await page.route(route, async request => {
      reading();
      await delay;
      await request.continue().catch(() => {});
    });
    const stopped = page.waitForEvent('requestfailed', {
      predicate: request => request.url().endsWith('/' + archiveId + '/backup'), timeout: 5000,
    });
    await page.locator('#content [data-action="archive-files"]').click();
    await pending;
    await page.keyboard.press('Escape');
    release();
    assert.match((await stopped).failure()?.errorText ?? '', /ERR_ABORTED/);
    await page.unroute(route);
    assert.equal(await page.locator(archiveDialog).count(), 0);
    await page.locator('#content [data-action="archive-files"]').click();
    await page.locator(archiveDialog + ' [data-action="archive-file"]').first().waitFor();
    assert.equal(await page.locator(archiveDialog + ' [data-action="archive-file"]').count(), 3);
    record.checks.push('closing the archive panel cancels its pending read and allows reopening');
    if (compatibility) {
      await page.keyboard.press('Escape');
      await page.locator('.task-item[data-task="' + compatibility.archive_id + '"]').click();
      if (RH_ARCHIVE_RETRY) {
        const path = '**/api/tasks/' + compatibility.archive_id + '/backup-files';
        await page.route(path, request => request.fulfill({
          status: 503, contentType: 'application/json', body: JSON.stringify({error: '测试：备份文件清单暂时无法读取'}),
        }), {times: 1});
      }
      await page.locator('#content [data-action="archive-files"]').click();
      const panel = page.locator(archiveDialog);
      const original = panel.getByRole('button', {name: '下载' + compatibility.filename + '，版本' + compatibility.version});
      if (RH_ARCHIVE_RETRY) {
        const retry = panel.getByRole('button', {name: '重新读取'});
        await retry.waitFor({timeout: 4000});
        assert.match(await panel.innerText(), /备份文件清单暂时无法读取/);
        await retry.focus();
        await page.keyboard.press('Enter');
        await original.waitFor();
        assert.ok(await panel.getByRole('button').first().evaluate(button => button === document.activeElement));
        record.checks.push('archive index retries in place and returns keyboard focus to the files');
      }
      await original.waitFor();
      assert.equal(await panel.locator('[data-action="archive-file"]').count(), 3);
      const saved = await download(page, original);
      assert.equal(saved.filename, compatibility.filename);
      assert.equal(saved.bytes.length, compatibility.bytes);
      assert.equal(sha256(saved.bytes), compatibility.sha256);
      record.checks.push('app recovers the original omitted by the earlier ZIP index');
    }
  });
  await workspace(false, async page => {
    await importBackup(page, exported);
    await page.reload();
    await page.locator(navigation).waitFor();
    await verifyFiles(page, exported, 'demo');
    const own = await page.evaluate(() => window.RH_DEMO.tasks.find(task => task.fixture === 'etf'));
    await importBackup(page, {workspaceVersion: 4, tasks: [own]});
    await page.locator('[data-action="archive-files"]:visible').click();
    const profiles = page.locator(archiveDialog + ' [data-action="archive-file"]');
    await profiles.first().waitFor();
    assert.equal(await profiles.count(), 2);
    assert.match(await profiles.nth(0).innerText(), /标准报告/);
    assert.match(await profiles.nth(1).innerText(), /详细报告/);
    assert.equal((await download(page, profiles.nth(0))).bytes.toString(), own.report_html);
    assert.equal((await download(page, profiles.nth(1))).bytes.toString(), own.report_detailed_html);
    record.checks.push('demo restores both standard and detailed saved HTML');
    await page.keyboard.press('Escape');

    const changed = structuredClone(exported);
    changed.reports[0].html = '<script>fetch("https://archive.invalid/")</script>';
    changed.originals[0].original_base64 = 'QQ==';
    await importBackup(page, changed);
    await page.locator('[data-action="archive-files"]:visible').click();
    const rows = page.locator(archiveDialog + ' [data-action="archive-file"]');
    let unwanted = 0;
    const onDownload = () => { unwanted++; };
    page.on('download', onDownload);
    await rows.nth(0).click();
    await page.locator('#archive-file-feedback:visible').filter({hasText: '指纹校验失败'}).waitFor();
    await rows.nth(2).click();
    await page.locator('#archive-file-feedback:visible').filter({hasText: '长度校验失败'}).waitFor();
    assert.equal(unwanted, 0);
    page.off('download', onDownload);
    assert.equal(sha256((await download(page, rows.nth(1))).bytes), exported.reports[1].html_sha256);
    record.checks.push('damaged files are rejected individually and other versions remain downloadable');
    await page.keyboard.press('Escape');
    await importBackup(page, {title: '旧备份'});
    await page.locator('[data-action="archive-files"]:visible').click();
    await page.locator(archiveDialog).filter({hasText: '没有保存可单独下载的报告或原件'}).waitFor();
    record.checks.push('legacy backup without file bytes opens as an empty file list');
    if (compatibility) {
      await page.keyboard.press('Escape');
      const saved = exported.originals[0];
      const oldTask = {...exported.tasks[0], materials: [{
        task_id: saved.task_id, research_version: saved.research_version, kind: 'material',
        snapshot: {name: saved.name, hash: saved.sha256, original_base64: saved.original_base64},
      }]};
      await importBackup(page, {workspaceVersion: 4, tasks: [oldTask], originals: []});
      await page.locator('[data-action="archive-files"]:visible').click();
      const original = page.locator(archiveDialog).getByRole('button', {
        name: '下载' + saved.name + '，版本' + saved.research_version,
      });
      assert.equal(sha256((await download(page, original)).bytes), saved.sha256);
      record.checks.push('standalone demo reads an original from a checkpoint with an empty JSON index');
    }
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
  await writeFile(RH_ARCHIVE_RECEIPT, JSON.stringify(record, null, 2));
  console.log(JSON.stringify(record));
}
