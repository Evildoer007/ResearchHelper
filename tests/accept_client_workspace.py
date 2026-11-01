"""Independent continuation of the OH UI suite for report inspection and Desk.

The existing suite is not edited. Its exact old citation step is extended in a
new test artifact to close the new, intentionally modal source reader before
resuming workspace operations. All other original checks run unchanged.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from tests.accept_native_workbench_render import RenderRuntime

ROOT = Path(__file__).resolve().parents[1]
NODE = Path('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')
OH = Path('/Users/haoranxu/Desktop/OptionHelper')


def browser_fixture(output):
    original = ROOT / 'tests/accept_packaged_oh_alignment.mjs'
    text = original.read_text()
    startup = "  check('fresh isolated offline Store',initial.offline===true&&initial.tasks.length===0&&Object.keys(initial.history).length===6);"
    assert text.count(startup) == 1
    text = text.replace(startup, startup + """
  check('no redundant research configuration button',await page.locator('[data-action=configure-model]').count()===0);
  const modelSettings=await settings();await section(modelSettings,'model');
  check('model configuration remains in Settings',await modelSettings.locator('#model-form').count()===1);
  await modelSettings.locator('.settings-close').click();
""")
    creation = "  await createCase('mechanism');const task=await createCase('etf');"
    assert text.count(creation) == 1
    text = text.replace(creation, creation + """
  const originalTask=JSON.stringify(await page.evaluate(()=>selected));
  await mode('desk');
  check('Desk initially selects working papers',await page.locator('#view-2').getAttribute('aria-selected')==='true');
  const thesis=page.locator('#content [data-desk-thesis]').first();
  check('each actual thesis has five working-record parts',JSON.stringify(await thesis.locator('.workingpaper-part>h3').allTextContents())===
    JSON.stringify(['理论依据','数据依据','分析与计算','反证与限制','原始来源']));
  const refs=await thesis.locator('[data-evidence]').evaluateAll(nodes=>nodes.map(node=>node.dataset.evidence));
  check('Desk references only this task evidence',refs.length>0&&refs.every(id=>task.evidence.some(item=>item.id===id)));
  const method=thesis.locator('[data-calculation]').first();await method.click();
  check('Desk method opens registered inputs',await page.locator('#dialog .calculation-inputs').isVisible());
  await page.keyboard.press('Escape');
  const source=thesis.locator('[data-evidence]').first();await source.click();
  check('Desk source opens actual original',await page.locator('#dialog[open]').innerText().then(text=>task.evidence.some(item=>text.includes(item.text))));
  await page.keyboard.press('Escape');
  check('viewing working papers never changes research',JSON.stringify(await page.evaluate(()=>selected))===originalTask);
  await mode('chat');
""")
    citation = "      await report.locator('.chart svg').waitFor();"
    assert text.count(citation) == 1
    text = text.replace(citation, """
      check(width+' '+theme+' client paper has no accordions',await report.locator('details,summary').count()===0);
      check(width+' '+theme+' citation opens independent reader',await report.locator('#report-inspector[open]').isVisible());
      await page.keyboard.press('Escape');
      check(width+' '+theme+' original closes without changing paper',await report.locator('#report-inspector').isHidden()&&await report.locator('#report-records').isHidden());
""" + citation)
    fixture = output / 'client-workspace-browser.mjs'
    fixture.write_text(text)
    return fixture, hashlib.sha256(original.read_bytes()).hexdigest()


def run():
    output = Path(tempfile.mkdtemp(prefix='client-workspace-', dir=ROOT / 'result/acceptance'))
    record = dict(status='failed', native_window_opened=False, production_store_touched=False,
        system_dialog_presented=False, model_calls=0, real_data_requests=0, installed=False,
        original_tests_modified=False, actual_model_quality_accepted=False)
    runtime = RenderRuntime(output, record)
    try:
        references = [OH / 'products/app/frontend/shared' / name for name in
                      ('styles.css', 'refinement.css', 'theme-overrides.css', 'choice-controls.css')]
        references += [OH / 'products/app/frontend/settings' / name for name in
                       ('settings-shell.css', 'general-settings.css')]
        record['oh_reference_hashes'] = {str(file): hashlib.sha256(file.read_bytes()).hexdigest() for file in references}
        for file in references:
            folder = 'ui' if file.parent.name == 'shared' else 'settings'
            assert file.read_bytes() == (ROOT / 'products/app/frontend' / folder / file.name).read_bytes()
        fixture, original_hash = browser_fixture(output)
        record['original_browser_suite_sha256'] = original_hash
        base = runtime.start()
        environment = dict(os.environ, RESEARCHHELPER_PACKAGE_URL=base, RESEARCHHELPER_ACCEPTANCE_DIR=str(output))
        completed = subprocess.run([str(NODE), str(fixture)], cwd=ROOT, env=environment,
            capture_output=True, text=True, timeout=240)
        (output / 'browser.log').write_text(completed.stdout + completed.stderr)
        record['browser'] = json.loads((output / 'browser-receipt.json').read_text())
        assert completed.returncode == 0 and record['browser']['status'] == 'passed', record['browser'].get('error')
        assert hashlib.sha256((ROOT / 'tests/accept_packaged_oh_alignment.mjs').read_bytes()).hexdigest() == original_hash
        for file, expected in record['oh_reference_hashes'].items():
            assert hashlib.sha256(Path(file).read_bytes()).hexdigest() == expected
        record['status'] = 'scoped_passed'
    except Exception as error:
        record['error'] = str(error)
    finally:
        try:
            runtime.stop()
        except Exception as error:
            record.update(status='failed', cleanup_error=str(error))
        receipt = output / 'receipt.json'
        receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(dict(status=record['status'], checks=len(record.get('browser', {}).get('checks', [])),
        receipt=str(receipt), error=record.get('error')), ensure_ascii=False))
    if record['status'] != 'scoped_passed':
        raise SystemExit(1)


if __name__ == '__main__':
    run()
