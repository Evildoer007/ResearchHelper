/* Exercise the two real retained file demos in fresh, offline browser contexts.
   No build, foreground window, production service, clipboard or system print. */
import {createRequire} from 'node:module';
import {mkdtemp, readFile, writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const {chromium} = require(path.join(process.env.HOME, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root = process.cwd();
const destinations = [path.join(root, 'demo'), '/Users/haoranxu/Desktop/demo/ResearchHelper demo'];
const output = await mkdtemp(path.join(root, 'result/acceptance/synced-demos-'));
const record = {status: 'failed', destinations, checks: [], screens: [], errors: [], externalRequests: [],
  headless: true, productionStoreTouched: false, systemClipboardTouched: false, systemPrintOpened: false,
  modelCalls: 0, realDataRequests: 0};
const hash = value => createHash('sha256').update(value).digest('hex');
const check = (name, value) => {assert.equal(value, true, name); record.checks.push(name)};
let browser;
try {
  browser = await chromium.launch({channel: 'chrome', headless: true, args: ['--disable-gpu']});
  for (let index = 0; index < destinations.length; index++) {
    const destination = destinations[index];
    const manifestBytes = await readFile(path.join(destination, 'manifest.json'));
    const manifest = JSON.parse(manifestBytes);
    check(index + ' release alias matches', hash(manifestBytes) === hash(await readFile(path.join(destination, 'researchhelper-release.json'))));
    for (const [file, expected] of Object.entries(manifest.files))
      assert.equal(hash(await readFile(path.join(destination, file))), expected, index + ' ' + file);
    check(index + ' full release inventory matches', true);
    const context = await browser.newContext({viewport: {width: 1440, height: 900}, reducedMotion: 'reduce'});
    await context.route('**/*', route => {
      if (route.request().url().startsWith('file:') || route.request().url().startsWith('data:')) return route.continue();
      record.externalRequests.push(route.request().url());
      return route.abort();
    });
    const page = await context.newPage();
    page.on('pageerror', error => record.errors.push(index + ' ' + error.message));
    await page.goto(pathToFileURL(path.join(destination, 'index.html')).href);
    await page.locator('#chat-form button[type=submit]:not([disabled])').waitFor();
    check(index + ' generated entry opens local workspace', page.url() === pathToFileURL(path.join(destination, 'public/index.html')).href);
    const selectMode = async mode => page.locator('[data-mode-switch] [data-mode=' + mode + ']').click();
    await selectMode('desk');
    for (let view = 0; view < 5; view++) {
      await page.locator('#view-' + view).click();
      check(index + ' empty view ' + view + ' opens', await page.locator('#view-' + view).getAttribute('aria-selected') === 'true');
    }
    await selectMode('chat');
    await page.locator('#chat-input').fill('离线草稿恢复检查');
    await page.reload();
    await page.locator('#chat-form button[type=submit]:not([disabled])').waitFor();
    check(index + ' draft survives refresh', await page.locator('#chat-input').inputValue() === '离线草稿恢复检查');
    await page.locator('#chat-input').fill('');
    const openMenu = async () => {
      if (await page.locator('[data-account-menu-toggle]').isHidden()) await page.locator('.web-control[data-action=tasks]').click();
      await page.locator('[data-account-menu-toggle]').click();
    };
    await openMenu();
    await page.locator('#account-menu [data-action=settings]').click();
    const settings = page.frameLocator('.workspace-settings-dialog iframe');
    await settings.locator('#preferences:not([hidden])').waitFor();
    check(index + ' file settings excludes credentials', await settings.locator('#model-form,#data-form,#execution-form').count() === 0);
    await settings.locator('.settings-nav a[href="#storage"]').click();
    await settings.locator('[data-workspace-action=cases]').click();
    await page.locator('#dialog[open]').waitFor();
    check(index + ' all eight entry fixtures available', await page.locator('[data-case]').count() === 8);
    await page.locator('[data-case=mechanism]').click();
    await page.locator('#dialog').waitFor({state: 'hidden'});
    await selectMode('desk');
    for (let view = 0; view < 5; view++) {
      await page.locator('#view-' + view).click();
      check(index + ' populated view ' + view + ' opens', await page.locator('#view-' + view).getAttribute('aria-selected') === 'true');
    }
    await page.locator('#view-3').click();
    const report = page.frameLocator('#report-frame');
    await report.locator('body').waitFor();
    check(index + ' frozen complete report available', (await report.locator('body').innerText()).length > 300);
    check(index + ' offline task remains synthetic', await page.evaluate(() => JSON.parse(localStorage.getItem('researchhelper-demo-draft-v2')).tasks.some(task => task.fixture === 'mechanism' && task.synthetic === true)));
    for (const viewport of [{width: 1440, height: 900}, {width: 1024, height: 768}, {width: 390, height: 844}]) {
      await page.setViewportSize(viewport);
      for (const theme of ['light', 'dark']) {
        await page.evaluate(theme => {document.documentElement.dataset.theme = theme; document.documentElement.classList.toggle('dark', theme === 'dark')}, theme);
        await page.locator('#view-0').click();
        check(index + ' no horizontal overflow ' + viewport.width + ' ' + theme, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        for (let view = 0; view < 5; view++) {
          const bounds = await page.locator('#view-' + view).boundingBox();
          check(index + ' visible view ' + view + ' ' + viewport.width + ' ' + theme, !!bounds && bounds.x >= 0 && bounds.x + bounds.width <= viewport.width && bounds.y >= 0 && bounds.y + bounds.height <= viewport.height);
        }
        const filename = index + '-desk-' + viewport.width + '-' + theme + '.png';
        await page.screenshot({path: path.join(output, filename), animations: 'disabled'});
        record.screens.push(filename);
      }
    }
    await page.setViewportSize({width: 1440, height: 900});
    await openMenu();
    await page.locator('#account-menu [data-action=library]').click();
    check(index + ' six complete historical reports retained', await page.locator('#dialog [data-history]').count() === 6);
    await page.locator('#dialog [data-history=semi]').click();
    await page.locator('#view-3').click();
    const history = page.frameLocator('#report-frame');
    await history.locator('h1').filter({hasText: 'AI产业趋势与估值'}).first().waitFor();
    const historyScreen = index + '-history-paper-1440.png';
    await page.screenshot({path: path.join(output, historyScreen), animations: 'disabled'});
    record.screens.push(historyScreen);
    await history.locator('details.research-appendix > summary').click();
    check(index + ' complete original research expands', await history.locator('details.research-appendix').getAttribute('open') !== null);
    check(index + ' full historical report reads offline', (await history.locator('body').innerText()).length > 1500);
    for (const key of ['ipo', 'broker', 'consumer', 'medicine', 'internet']) {
      await openMenu();
      await page.locator('#account-menu [data-action=library]').click();
      await page.locator('#dialog [data-history=' + key + ']').click();
      await page.locator('#view-3').click();
      const original = page.frameLocator('#report-frame');
      await original.locator('meta[name=rh-source-key][content=' + key + ']').waitFor({state: 'attached'});
      await original.locator('details.research-appendix > summary').click();
      check(index + ' complete original report available ' + key,
        await original.locator('details.research-appendix').getAttribute('open') !== null &&
        (await original.locator('.archived-content').innerText()).length > 1000);
    }
    await page.locator('#backup-file').setInputFiles({name: 'legacy.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({question: '旧任务仅归档', status: '已完成', reviewed: true}))});
    await page.waitForFunction(() => JSON.parse(localStorage.getItem('researchhelper-demo-draft-v2')).tasks.some(task => task.question === '旧任务仅归档'));
    check(index + ' old success does not become newly verified', await page.evaluate(() => {const task = JSON.parse(localStorage.getItem('researchhelper-demo-draft-v2')).tasks.find(task => task.question === '旧任务仅归档'); return task.status === '待开始' && task.evidence.length === 0 && !!task.legacy_backup}));
    await page.reload();
    await page.locator('#chat-form button[type=submit]:not([disabled])').waitFor();
    check(index + ' imported archive survives reload', await page.locator('#task-title').innerText() === '旧任务仅归档');
    await context.close();
  }
  check('no script errors', record.errors.length === 0);
  check('no external requests', record.externalRequests.length === 0);
  record.status = 'passed';
} catch (error) {
  record.error = error.stack;
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  record.browserClosed = true;
  await writeFile(path.join(output, 'receipt.json'), JSON.stringify(record, null, 2));
  console.log(JSON.stringify({status: record.status, checks: record.checks.length, receipt: path.join(output, 'receipt.json'), error: record.error}));
}
