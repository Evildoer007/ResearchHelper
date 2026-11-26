/* Evaluate the exact scripts the Windows shell passes to WebView2. */
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const shell = readFileSync(path.join(__dirname, '../desktop/windows/Program.cs'), 'utf8');
const requestScript = shell.match(/CurrentReportPrintRequest = "([^"]+)";/)?.[1];
const prepareScript = shell.match(/PrepareReportDocument = """\r?\n([\s\S]+?)""";/)[1];
const reportUrl = 'http://127.0.0.1:43123/api/tasks/test/report/2?profile=detailed';
const digest = 'a'.repeat(64);

function runPreparation({ title = true, main = true, type = 'text/html', ready = 'complete',
  variant = 'detailed', marker = true, exampleKey = null, example = null,
  expectedVariant = 'detailed', expectedDigest = digest, contentHash = digest,
  printing = true, profile = null, canApply = true, invalidProfile = false } = {}) {
  const events = [];
  const elements = {
    main,
    h1: title,
    'script#rh-publication-document[type="application/json"]': marker && {
      textContent: JSON.stringify({schema:'researchhelper.presentation-document',version:1,profile:variant,content_hash:contentHash})
    },
    'meta[name="rh-source-key"]': exampleKey && { content: exampleKey },
  };
  const window = {
    dispatchEvent(event) { events.push(event.type); },
    RHVisuals: canApply ? {
      applyProfile(value) {
        if (invalidProfile) throw Error('Unknown chart scheme');
        events.push(value);
      },
    } : undefined,
  };
  const document = { contentType: type, readyState: ready, body: { dataset: { reportVariant: variant } },
    querySelector(selector) { return elements[selector] ?? null; } };
  const script = vm.runInNewContext(prepareScript, { document, window, Event });
  return { ready: script(example, expectedVariant, expectedDigest, printing, profile), events };
}

test('the Windows menu reads the workbench request and never falls back to a hidden iframe', () => {
  assert.ok(requestScript);
  const document = { querySelector() { throw Error('The shell must never read an iframe directly'); } };
  assert.equal(vm.runInNewContext(requestScript, { window: {}, document }), null);
  const window = { ResearchHelperNative: { printRequest: () => null } };
  assert.equal(vm.runInNewContext(requestScript, { window, document }), null);
  const chosen = { url: reportUrl, digest, presentation: { theme: 'porcelain', schemes: { comparison: 'ranked_bar' } } };
  window.ResearchHelperNative.printRequest = () => chosen;
  assert.equal(vm.runInNewContext(requestScript, { window, document }), chosen);
  assert.match(shell, /ExecuteScriptAsync\(CurrentReportPrintRequest\)/);
});

test('print preparation applies the displayed palette and chart scheme before beforeprint', () => {
  const profile = { theme: 'porcelain', schemes: { comparison: 'ranked_bar' } };
  assert.deepEqual(runPreparation({ profile }), { ready: true, events: [profile, 'beforeprint'] });
  assert.deepEqual(runPreparation({ variant: 'standard', expectedVariant: 'standard' }),
    { ready: true, events: ['beforeprint'] });
});

test('loading, invalid HTML, a different variant and a different edition are withheld', () => {
  for (const options of [{ ready: 'interactive' }, { main: false }, { title: false },
    { type: 'application/json' }, { marker: false }, { variant: 'standard' },
    { contentHash: 'b'.repeat(64) }, { expectedDigest: null }]) {
    assert.deepEqual(runPreparation(options), { ready: false, events: [] }, JSON.stringify(options));
  }
});

test('history examples require their exact source marker and print with their own styling', () => {
  const history = { example: 'semi', exampleKey: 'semi', marker: false, expectedVariant: null };
  assert.deepEqual(runPreparation(history), { ready: true, events: ['beforeprint'] });
  assert.deepEqual(runPreparation({ ...history, exampleKey: 'broker' }), { ready: false, events: [] });
});

test('a missing or rejected presentation never triggers print preparation', () => {
  const profile = { theme: 'porcelain', schemes: { comparison: 'ranked_bar' } };
  assert.deepEqual(runPreparation({ profile, canApply: false }), { ready: false, events: [] });
  assert.deepEqual(runPreparation({ profile, invalidProfile: true }), { ready: false, events: [] });
});

test('preview checks the same frozen edition without changing print state', () => {
  assert.deepEqual(runPreparation({ printing: false }), { ready: true, events: [] });
  assert.deepEqual(runPreparation({ printing: false, expectedDigest: null }), { ready: false, events: [] });
});

test('a completed WebView2 navigation must match the requested document before the system dialog', () => {
  assert.match(shell, /navigation\.Cancel = !document\.Start\(navigation\.Uri, navigation\.NavigationId\)/);
  assert.match(shell, /document\.Owns\(navigation\.NavigationId\)[\s\S]*document\.CanShow\(core\.Source, navigation\.IsSuccess, navigation\.HttpStatusCode\)/);
  assert.match(shell, /await receipt\.Task\.WaitAsync\(deadline\.Token\)[\s\S]*ExecuteScriptAsync\(prepare\)[\s\S]*prepared != "true" \|\| !document\.Matches\(core\.Source\)[\s\S]*core\.ShowPrintUI/);
  assert.match(shell, /CloseReportWindows\(\)[\s\S]*activeReportPrint\?\.Cancel\(\)/);
});
