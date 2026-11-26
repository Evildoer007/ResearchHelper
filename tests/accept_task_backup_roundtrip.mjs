import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile, writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const require = createRequire(import.meta.url);
const {chromium} = require(path.join(process.env.HOME, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const {RH_BACKUP_BASE, RH_BACKUP_DEMO, RH_BACKUP_RECEIPT} = process.env;
const record = {status: 'failed', checks: [], screens: [], layouts: [], errors: [], externalRequests: [], browser_closed: false};
const storageKey = 'researchhelper-demo-draft-v2';
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
let browser;

async function checkWorkspace(online) {
  const name = online ? 'app' : 'demo';
  const context = await browser.newContext({acceptDownloads: true, viewport: {width: 1440, height: 900}, reducedMotion: 'reduce'});
  const base = online ? new URL(RH_BACKUP_BASE) : null;
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
      const login = await page.request.post(new URL('/api/auth/login', base).href, {
        headers: {'X-ResearchHelper-Token': token},
        data: {account: 'admin', password: '66666666', remember: false},
      });
      assert.equal(login.ok(), true);
    }
    await page.goto(online ? base.href : pathToFileURL(path.join(RH_BACKUP_DEMO, 'index.html')).href);
    await page.locator('.workspace-shell[data-initializing="false"]').waitFor();
    const task = await page.evaluate(() => tasks.find(item => item.fixture === 'etf' && item.delivery));
    assert.ok(task);
    const rail = page.locator('.task-item[data-task="' + task.id + '"]');
    await rail.click();
    await page.waitForFunction(id => selected?.id === id, task.id);
    await page.locator('[data-mode-switch] [data-mode="desk"]').click();
    await page.locator('#view-3').click();
    await page.locator('#content [data-work-menu-toggle]').click();
    const getDownload = async control => {
      const pending = page.waitForEvent('download');
      await control();
      const download = await pending;
      assert.equal(download.suggestedFilename(), 'ResearchHelper任务备份.json');
      return readFile(await download.path());
    };
    const backup = await getDownload(() => page.locator('#report-more-menu [data-action="backup"]').click());
    assert.ok(backup.length > 2_000_000);
    const payload = JSON.parse(backup);
    assert.equal(payload.tasks[0].id, task.id);
    if (online) {
      assert.deepEqual(payload.reports.map(item => item.version), [1, 2]);
      assert.ok(payload.originals[0].original_base64);
    } else {
      assert.equal(payload.tasks[0].report_html, task.report_html);
      assert.equal(payload.tasks[0].report_detailed_html, task.report_detailed_html);
    }
    record.checks.push(name + ' large backup includes saved reports');
    const persisted = online ? page.waitForResponse(response => response.url().endsWith('/api/workspace-draft') &&
      JSON.parse(response.request().postData() || '{}').state?.activeTaskId?.startsWith('rh_') &&
      JSON.parse(response.request().postData() || '{}').state.activeTaskId !== task.id && response.ok()) : null;
    await page.locator('#backup-file').setInputFiles({name: 'task.json', mimeType: 'application/json', buffer: backup});
    const archiveFiles = page.locator('[data-action="archive-files"]:visible');
    await archiveFiles.waitFor();
    assert.equal(await page.locator('#task-title').textContent(), task.question);
    assert.equal(await page.locator('#report-frame').count(), 0);
    assert.equal(await page.locator('#chat-form').isHidden(), true);
    const archive = await page.evaluate(() => selected);
    assert.equal(archive.parent, 'legacy-import');
    assert.equal(archive.delivery ?? null, null);
    assert.deepEqual(archive.evidence, []);
    if (persisted) await persisted;
    await page.reload();
    await archiveFiles.waitFor();
    assert.equal(await page.locator('[data-action="backup"]:visible').count(),0);
    assert.equal(await page.evaluate(() => selected.id), archive.id);
    record.checks.push(name + ' reload restores the passive archive');
    await archiveFiles.press('Enter');
    const retrieve = page.locator('#dialog[open] [data-action="backup"]');
    await retrieve.waitFor();
    for (const [width, height] of [[1440, 900], [1024, 768], [390, 844]]) {
      await page.setViewportSize({width, height});
      assert.equal(await retrieve.isVisible(), true);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      record.layouts.push({surface: name, width, height});
    }
    const restored = await getDownload(async () => { await retrieve.focus(); await page.keyboard.press('Enter'); });
    assert.equal(digest(restored), digest(backup));
    record.checks.push(name + ' keyboard download returns the identical backup');
    await page.keyboard.press('Escape');
    if (!online) {
      const saved = await page.evaluate(key => localStorage.getItem(key), storageKey);
      assert.ok(JSON.parse(saved).tasks.find(item => item.id === archive.id).legacy_backup_gzip);
      await page.evaluate(key => {
        const setItem = Storage.prototype.setItem;
        Storage.prototype.setItem = function (name, value) {
          if (name === key) throw new DOMException('fixture', 'QuotaExceededError');
          return setItem.call(this, name, value);
        };
      }, storageKey);
      await page.locator('#backup-file').setInputFiles({name: 'legacy.json', mimeType: 'application/json',
        buffer: Buffer.from('{"title":"空间不足的导入"}')});
      await page.locator('#notice').filter({hasText: '备份未导入'}).waitFor();
      assert.equal(await page.evaluate(() => selected.id), archive.id);
      assert.equal(await page.evaluate(key => localStorage.getItem(key), storageKey), saved);
      record.checks.push('demo storage failure preserves the selected archive and saved drafts');
    }
  } finally {
    await context.close();
  }
}

try {
  browser = await chromium.launch({channel: 'chrome', headless: true, args: ['--disable-gpu']});
  if (RH_BACKUP_BASE) await checkWorkspace(true);
  if (RH_BACKUP_DEMO) await checkWorkspace(false);
  assert.deepEqual(record.errors, []);
  assert.deepEqual(record.externalRequests, []);
  record.status = 'passed';
} catch (error) {
  record.error = error.message;
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  record.browser_closed = true;
  await writeFile(RH_BACKUP_RECEIPT, JSON.stringify(record, null, 2));
  console.log(JSON.stringify(record));
}
