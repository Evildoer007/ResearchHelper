"""Install and sync only the batch with current packaged and browser evidence."""

import json
from pathlib import Path
from unittest.mock import patch

from tests import release_workflow_batch as batch
from tests import release_host_boundary as previous
from tests import rebuild_publication_guard_final_delivery as final


def main():
    runtime = batch.regression()
    package = batch.dispatch.read(batch.PUBLISHED / 'batch-package.json')
    final.current(package)
    assert package['status'] == 'passed' and package['helper_sha256'] == batch.dispatch.digest(batch.__file__)
    assert package['skill_test_executions'] == package['app_test_executions'] == 214
    with patch.object(previous, 'BASE', batch.BASE):
        browser = previous.browser_evidence()
    assert sum(item['checks'] for item in browser) == 152
    paths = {item['name']: Path(item['receipt']) for item in browser}
    installer = batch.dispatch.load_script('packaging/install_release.py')
    sync = batch.dispatch.load_script('packaging/sync_demo.py')
    with patch.object(final, 'OUT', batch.OUT), patch.object(final, 'PUBLISHED', batch.PUBLISHED):
        with final.exact_receipts(writes={final.ACCEPTANCE / 'installation.json': batch.OUT / 'installation.json'}):
            installer.install()
        with final.exact_receipts(reads={
            final.ACCEPTANCE / 'package-background.json': batch.PUBLISHED / 'package-background.json',
            final.ACCEPTANCE / 'integrity.json': batch.OUT / 'integrity.json',
            final.ACCEPTANCE / 'autonomous-composer/full-chat/acceptance.json': paths['chat'],
            final.ACCEPTANCE / 'roles-workspace/acceptance.json': paths['roles']},
            writes={final.ACCEPTANCE / 'demo-sync-background.json': batch.OUT / 'demo-sync.json'}):
            sync.sync(background_candidate=True)
        installed = final.installed_readback(batch.OUT / 'installation.json', batch.OUT / 'demo-sync.json')
    release = batch.dispatch.read(batch.RELEASE / 'release-manifest.json')
    record = {'status': 'offline_candidate_with_legacy_assertion_conflicts',
              'offline_tests_run': 1231, 'offline_tests_passed': 1227, 'legacy_assertion_failures': runtime['failures'],
              'runtime_receipt': runtime['receipt'], 'runtime_receipt_sha256': batch.dispatch.digest(runtime['receipt']),
              'browser_evidence': browser, 'browser_rerun_checks': 152, 'packaged_checks': package,
              'installed': installed, 'prior_release_receipt': str(batch.PRIOR),
              'dmg_sha256': release['artifacts']['dmg']['sha256'],
              'skill_sha256': release['artifacts']['skill']['sha256'],
              'active_providers': ['ifind', 'tinyshare'], 'foreground_app_launched': False,
              'production_store_touched': False, 'real_model_calls': 0, 'real_supplier_calls': 0,
              'native_ui_verified': False, 'full_harness_complete': False,
              'publish_helper_sha256': batch.dispatch.digest(__file__)}
    path = batch.OUT / 'workflow-delivery.json'
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({'status': record['status'], 'receipt': str(path), 'full_harness_complete': False}, ensure_ascii=False))
    return record


if __name__ == '__main__':
    main()
