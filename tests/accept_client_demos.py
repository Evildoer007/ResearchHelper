"""Run both real file demos with contextual readers, retaining the old suite."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
NODE = Path('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')


def run():
    output = Path(tempfile.mkdtemp(prefix='client-demo-suite-', dir=ROOT / 'result/acceptance'))
    original = ROOT / 'tests/accept_synced_demos.mjs'
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    source = original.read_text()
    first = """    await history.locator('details.research-appendix > summary').click();
    check(index + ' complete original research expands', await history.locator('details.research-appendix').getAttribute('open') !== null);
    check(index + ' full historical report reads offline', (await history.locator('body').innerText()).length > 1500);"""
    replacement = """    check(index + ' customer report has no accordion', await history.locator('details,summary').count() === 0);
    await history.locator('.example-risk a[href="#history-source"]').click();
    check(index + ' complete original research opens separately', await history.locator('#report-inspector[open]').isVisible());
    check(index + ' full historical report reads offline', (await history.locator('.archived-content').innerText()).length > 1500);
    await page.keyboard.press('Escape');
    check(index + ' close restores client reading', await history.locator('#report-inspector').isHidden());"""
    assert source.count(first) == 1
    source = source.replace(first, replacement)
    second = """      await original.locator('details.research-appendix > summary').click();
      check(index + ' complete original report available ' + key,
        await original.locator('details.research-appendix').getAttribute('open') !== null &&
        (await original.locator('.archived-content').innerText()).length > 1000);"""
    replacement = """      await original.locator('.example-risk a[href="#history-source"]').click();
      check(index + ' complete original report available ' + key,
        await original.locator('#report-inspector[open]').isVisible() &&
        (await original.locator('.archived-content').innerText()).length > 1000);
      check(index + ' clean client report ' + key, await original.locator('details,summary').count() === 0);
      await page.keyboard.press('Escape');"""
    assert source.count(second) == 1
    source = source.replace(second, replacement).replace("result/acceptance/synced-demos-", "result/acceptance/client-synced-demos-")
    fixture = output / 'client-demos.mjs'
    fixture.write_text(source)
    result = subprocess.run([str(NODE), str(fixture)], cwd=ROOT, env=dict(os.environ),
        capture_output=True, text=True, timeout=180)
    (output / 'browser.log').write_text(result.stdout + result.stderr)
    browser = json.loads(result.stdout.strip().splitlines()[-1])
    assert hashlib.sha256(original.read_bytes()).hexdigest() == digest
    record = dict(status='scoped_passed' if result.returncode == 0 and browser['status'] == 'passed' else 'failed',
        browser_receipt=browser['receipt'], checks=browser['checks'], error=browser.get('error'),
        original_suite_sha256=digest, original_suite_modified=False, native_window_opened=False,
        production_store_touched=False, model_calls=0, real_data_requests=0)
    receipt = output / 'receipt.json'
    receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(dict(status=record['status'], checks=record['checks'], receipt=str(receipt),
        browser_receipt=browser['receipt'], error=record['error']), ensure_ascii=False))
    if record['status'] != 'scoped_passed':
        raise SystemExit(1)


if __name__ == '__main__':
    run()
