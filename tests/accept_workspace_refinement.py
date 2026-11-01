"""Independent UI refinement acceptance; existing suites remain unchanged.

Uses an isolated offline Store and the existing App/client regression suite.
No real provider/model calls, clipboard access, or system print dialogs.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from tests.accept_client_workspace import browser_fixture
from tests.accept_native_workbench_render import RenderRuntime

ROOT = Path(__file__).resolve().parents[1]
NODE = Path('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')

EXTRA_CHECKS = r'''
  await mode('desk');await page.locator('#view-4').click();
  check('empty tracking is one actionable state',await page.locator('.tracking-empty h2').innerText()==='暂无跟踪条件');
  check('tracking history is collapsed initially',await page.locator('.tracking-history').getAttribute('open')===null);
  check('redundant gap navigation buttons removed',await page.locator('[data-gap-process],[data-gap-calls],[data-gap-material]').count()===0);
  const avatar=page.locator('.assistant-toggle svg[data-bloub]');await avatar.waitFor();
  check('OH Bloub replaces floating brand mark',await page.locator('.assistant-toggle img').count()===0&&await avatar.locator('[data-bloub-body]').getAttribute('d').then(Boolean));
  const frozenTask=JSON.stringify(await page.evaluate(()=>selected));
  await page.locator('.tracking-empty [data-action=tracking-input]').click();
  check('tracking action opens same conversation',await page.locator('#assistant-panel').getAttribute('aria-hidden')==='false'&&await page.locator('#chat-input').evaluate(el=>el===document.activeElement));
  await page.locator('[data-action=assistant-close]').click();
  check('assistant closes back to its own focus target',await page.locator('.assistant-toggle').evaluate(el=>el===document.activeElement));
  check('opening tracking never mutates research',JSON.stringify(await page.evaluate(()=>selected))===frozenTask);
  await page.locator('.tracking-history summary').click();
  check('version history still opens',await page.locator('.tracking-history').getAttribute('open')!==null);
  await page.screenshot({path:path.join(output,'tracking-1440.png')});record.screens.push('tracking-1440.png');
  await page.locator('#view-3').click();
  check('secondary report actions hidden until requested',await page.locator('[data-action=print]').isHidden()&&await page.locator('[data-action=visual-library]').isHidden()&&await page.locator('#content [data-action=versions]').isHidden());
  check('export format does not span the workspace',await page.locator('.report-export-actions .choice-trigger').evaluate(el=>{const box=el.getBoundingClientRect();return box.width>=80&&box.width<=108&&box.height>=44}));
  const formatChoice=page.locator('.report-export-actions .choice-trigger');
  await formatChoice.click();await page.keyboard.press('End');await page.keyboard.press('Enter');
  check('report format keyboard reaches CSV',await page.locator('#report-format').inputValue()==='csv');
  await formatChoice.click();await page.keyboard.press('Home');await page.keyboard.press('Enter');
  check('report format keyboard restores HTML',await page.locator('#report-format').inputValue()==='html');
  const menu=page.locator('[data-work-menu-toggle]');await menu.focus();await page.keyboard.press('ArrowDown');
  check('report menu arrow key reaches first item',await page.locator('#report-more-menu [data-action=print]').evaluate(el=>el===document.activeElement));
  await page.keyboard.press('End');
  check('report menu End reaches version action',await page.locator('#report-more-menu [data-action=versions]').evaluate(el=>el===document.activeElement));
  await page.keyboard.press('Escape');
  check('report menu Escape closes and restores focus',await page.locator('#report-more-menu').isHidden()&&await menu.evaluate(el=>el===document.activeElement));
  await menu.click();await page.locator('#report-more-menu [data-action=visual-library]').click();
  check('chart schemes remain reachable in overflow menu',await page.locator('#dialog[open]').isVisible());
  await page.keyboard.press('Escape');
  check('report menu closes after action',await page.locator('#report-more-menu').isHidden());
  await page.screenshot({path:path.join(output,'report-1440.png')});record.screens.push('report-1440.png');
  await mode('chat');
'''


def run():
    parent = ROOT / 'result/acceptance/workspace-refinement'
    parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='browser-', dir=parent))
    record = dict(status='failed', native_window_opened=False,
        production_store_touched=False, system_dialog_presented=False,
        model_calls=0, real_data_requests=0, installed=False,
        original_tests_modified=False, actual_model_quality_accepted=False)
    runtime = RenderRuntime(output, record)
    original = ROOT / 'tests/accept_packaged_oh_alignment.mjs'
    original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    try:
        fixture, source_hash = browser_fixture(output)
        text = fixture.read_text()
        anchor = "  await page.locator('#chat-input').fill('刷新后保留的未发送研究问题');"
        assert text.count(anchor) == 1
        text = text.replace(anchor, EXTRA_CHECKS + anchor)
        # The current data-rich chart has numeric bars plus a scatter marker layer.
        # Keep the old test intact and validate both layers in this new suite.
        old_chart = "const chart=echarts.getInstanceByDom(el),values=chart.getOption().series.flatMap(series=>series.data.map(point=>point.value));\n        return values.length===2&&values[0]===.2&&values[1]===.6&&el.getBoundingClientRect().width>0;"
        new_chart = "const chart=echarts.getInstanceByDom(el),series=chart.getOption().series,bars=series.find(s=>s.type==='bar'),markers=series.find(s=>s.type==='scatter');\n        return JSON.stringify(bars?.data)===JSON.stringify([.2,.6])&&JSON.stringify(markers?.data)===JSON.stringify([[.2,0],[.6,1]])&&el.getBoundingClientRect().width>0;"
        assert text.count(old_chart) == 1
        text = text.replace(old_chart, new_chart)
        fixture.write_text(text)
        record['original_browser_suite_sha256'] = source_hash
        base = runtime.start()
        environment = dict(os.environ, RESEARCHHELPER_PACKAGE_URL=base,
            RESEARCHHELPER_ACCEPTANCE_DIR=str(output))
        completed = subprocess.run([str(NODE), str(fixture)], cwd=ROOT,
            env=environment, capture_output=True, text=True, timeout=240)
        (output/'browser.log').write_text(completed.stdout+completed.stderr)
        record['browser'] = json.loads((output/'browser-receipt.json').read_text())
        assert completed.returncode == 0 and record['browser']['status'] == 'passed', record['browser'].get('error')
        assert hashlib.sha256(original.read_bytes()).hexdigest() == original_hash
        record['status'] = 'scoped_passed'
    except Exception as error:
        record['error'] = str(error)
    finally:
        try:
            runtime.stop()
        except Exception as error:
            record.update(status='failed', cleanup_error=str(error))
        receipt = output/'receipt.json'
        receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(dict(status=record['status'], checks=len(record.get('browser',{}).get('checks',[])),
        receipt=str(receipt), error=record.get('error')), ensure_ascii=False))
    if record['status'] != 'scoped_passed':
        raise SystemExit(1)


if __name__ == '__main__':
    run()
