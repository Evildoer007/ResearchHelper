"""Extend byte readback with actual post-install evidence; preserve each receipt."""

from pathlib import Path

from tests import release_research_contract_delivery as delivery
from tests.review_api_value_reference_readback import latest, verify_files


def run():
    base, out = delivery.BASE, delivery.OUT
    initial_path = out / 'completion-readback.json'
    initial = delivery.read(initial_path)
    assert initial['status'] == 'passed'
    assert initial['readback_helper_sha256'] == delivery.digest(
        delivery.ROOT / 'core/tests/review_research_contract_delivery_readback.py')
    current_path = out / 'research-contract-delivery.json'
    current = delivery.read(current_path)
    assert initial['delivery_receipt_sha256'] == delivery.digest(current_path)
    runtime = delivery.source_gate()
    counts = {
        'source_gate_input_files': verify_files(delivery.ROOT, runtime['source_hashes']),
        'shipping_source_files': verify_files(delivery.ROOT, current['source_hashes']),
        'installed_app_files': verify_files('/Applications/ResearchHelper.app',
            delivery.read(delivery.RELEASE / 'app-manifest.json')['files']),
        'installed_skill_files': verify_files(delivery.ROOT / '.skills/researchhelper',
            delivery.read(delivery.RELEASE / 'researchhelper/manifest.json')['files']),
        'desktop_demo_files': verify_files('/Users/haoranxu/Desktop/demo/ResearchHelper demo',
            delivery.read(delivery.RELEASE / 'demo/manifest.json')['files']),
    }
    assert counts == initial['counts']
    pointer = delivery.read(base / 'installed-six-public-documents/completed.json')
    report_path, report = latest(Path(pointer['directory']), 'browser-*/receipt.json')
    demo_path, demo = latest(base, 'offline-demo-*/receipt.json')
    frozen_path, frozen = latest(base / 'frozen-worker-after-install', 'review-*/isolated-transport-package.json')
    assert report['status'] == demo['status'] == frozen['status'] == 'passed'
    assert len(report['checks']) == 24 and len(demo['checks']) == 2 and len(frozen['checks']) == 5
    for browser in (report, demo):
        assert not browser['errors'] and not browser['external_attempts'] and browser['browser_stopped']
    assert frozen['dmg_sha256'] == current['dmg_sha256']
    assert frozen['current_release_receipt_sha256'] == delivery.digest(current_path)
    assert frozen['source_reads_denied'] and frozen['production_reads_denied']
    assert not frozen['real_model_called'] and not frozen['real_provider_called']
    assert all(frozen[key] for key in ('backend_stopped', 'fixture_server_stopped', 'mount_detached', 'temporary_stage_removed'))
    assert delivery.read(out / 'production-before.json') == delivery.production_files()
    result = initial | {'counts': counts, 'earlier_readback_sha256': delivery.digest(initial_path),
        'report_browser_checks': 24, 'desktop_demo_checks': 2, 'copied_dmg_isolation_checks': 5,
        'report_browser_receipt': str(report_path), 'desktop_demo_receipt': str(demo_path),
        'postinstall_frozen_worker_receipt': str(frozen_path),
        'postinstall_fixture_is_autonomous_research': False,
        'finalization_helper_sha256': delivery.digest(__file__), 'finalization_helper_counted_in_source_inventory': False}
    destination = out / 'extended-completion-readback.json'
    assert not destination.exists(), 'Keep the completed evidence intact'
    delivery.save(destination, result)
    return {key: result[key] for key in ('status', 'counts', 'report_browser_checks',
        'desktop_demo_checks', 'copied_dmg_isolation_checks', 'production_unchanged', 'full_harness_complete')}
