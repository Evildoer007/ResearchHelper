"""Preserve all58 settings assertions, waiting for logout navigation first."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from tests import ROOT
from tests import accept_planning_effort_view as original
from tests.accept_native_workbench_render import RenderRuntime
from tests.accept_default_app_need_driven_filings import protected_configuration
from tests.accept_default_app_online_research import archive_snapshot


def run():
    output = Path(tempfile.mkdtemp(prefix='planning-effort-navigation-', dir=ROOT / 'result/acceptance'))
    source = ROOT / 'tests/accept_planning_effort_view.mjs'
    text = source.read_text()
    anchor = "await page.locator('#account-menu [data-action=logout]').click();"
    assert text.count(anchor) == 1
    text = text.replace(anchor, anchor + "\n  await page.locator('#login-account').waitFor();")
    ready_anchor = "const settings=async(section='model')=>{"
    assert text.count(ready_anchor) == 1
    text = text.replace(ready_anchor, ready_anchor + "\n    await page.waitForFunction(()=>typeof workspaceReady!=='undefined'&&workspaceReady&&!initializing&&!leaving);")
    login_anchor = "await page.locator('#login-submit').click();await page.locator('#chat-input').waitFor();"
    assert text.count(login_anchor) == 1
    text = text.replace(login_anchor, login_anchor + "\n    await page.waitForFunction(account=>typeof workspaceReady!=='undefined'&&workspaceReady&&!initializing&&!leaving&&boot.account===account,account);")
    generated = output / 'planning-effort-navigation.mjs'
    generated.write_text(text)
    before = protected_configuration(), archive_snapshot()
    references = {str(original.OH / name): hashlib.sha256((original.OH / name).read_bytes()).hexdigest()
        for name in ('shared/choice-controls.css', 'settings/general-settings.css')}
    record = dict(status='failed', native_window_opened=False, model_calls=0, provider_calls=0,
        real_configuration_changed=False, research_accepted=False, oh_references=references,
        original_browser_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), expected_checks=58)
    runtime = RenderRuntime(output, record)
    try:
        url = runtime.start()
        environment = dict(os.environ, RESEARCHHELPER_PACKAGE_URL=url, RESEARCHHELPER_ACCEPTANCE_DIR=str(output))
        completed = subprocess.run([str(original.NODE), str(generated)], cwd=ROOT, env=environment,
            capture_output=True, text=True, timeout=120)
        (output / 'browser.log').write_text(completed.stdout + completed.stderr)
        record['browser'] = json.loads((output / 'browser-receipt.json').read_text())
        assert completed.returncode == 0 and record['browser']['status'] == 'passed', record['browser'].get('error')
        assert len(record['browser']['checks']) == 58
        assert hashlib.sha256(source.read_bytes()).hexdigest() == record['original_browser_sha256']
        record['status'] = 'scoped_passed'
    except BaseException as error:
        record.update(error_type=type(error).__name__, error=str(error))
        raise
    finally:
        runtime.stop()
        record.update(production_unchanged=before == (protected_configuration(), archive_snapshot()),
            oh_unchanged=all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == value
                             for path, value in references.items()))
        target = output / 'receipt.json'
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        print(json.dumps(dict(status=record['status'], receipt=str(target), error=record.get('error')), ensure_ascii=False))
    return record
