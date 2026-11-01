"""Visible partial findings and original receipt coverage, strictly offline."""
import copy
from html.parser import HTMLParser
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
try:
    import test_reviewed_partial_answer as partial
except ModuleNotFoundError:
    from tests import test_reviewed_partial_answer as partial
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import snapshot_record, _record_current_acquisition
from researchhelper.provenance import collection_coverage
from researchhelper.reports import compose, render


class MainPaper(HTMLParser):
    def __init__(self, page):
        super().__init__(); self.inside = False; self.text = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        if tag == 'main': self.inside = True

    def handle_endtag(self, tag):
        if tag == 'main': self.inside = False

    def handle_data(self, value):
        if self.inside: self.text.append(value)


class VisiblePartialFindingTests(unittest.TestCase):
    setUp = partial.ReviewedPartialAnswerTests.setUp
    reviewed = partial.ReviewedPartialAnswerTests.reviewed

    def test_reviewed_partial_answer_is_in_visible_paper_not_only_hidden_records(self):
        task = self.reviewed()
        page = self.session.archive.report(task['id'], task['version'])['html']
        visible = ''.join(MainPaper(page).text)
        self.assertIn(self.proposal['answers']['q1']['answer'], visible)
        self.assertIn('已核实部分', visible)
        self.assertEqual(task['questions'][0]['status'], '未回答')

    def test_unsupported_prose_is_not_added_to_visible_partial_paper(self):
        task = self.reviewed(supported=False)
        page = self.session.archive.report(task['id'], task['version'])['html']
        self.assertNotIn('已核实部分', ''.join(MainPaper(page).text))


class CollectionCoverageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='rh-collection-')
        self.addCleanup(temporary.cleanup)
        self.agent = ResearchAgent(ResearchArchive(temporary.name), ROOT, offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create('获取证券行业2026年半年报营业收入与归母净利润', research_mode='current')
        parameters = dict(searchtype='stock', searchstring=
            '证券行业股票，2026年半年报营业收入，2026年半年报归属于母公司所有者的净利润')
        payload = dict(errorcode=0, tables=[dict(table={
            '股票代码': ['600030.SH', '600999.SH'], '股票简称': ['合成甲', '合成乙'],
            '所属同花顺行业': ['合成分类', '合成分类'],
            '营业收入[20260630]': [0, None], '归属于母公司所有者的净利润[20260630]': [-987654321, 2]})])
        self.source = snapshot_record(self.task, 'ifind', 'smart_stock_picking', parameters, payload)
        self.task['source_records'].append(self.source)
        event = dict(kind='data_requests', batch=self.task['batch'], status='成功',
            fingerprint=self.source['request_hash'], transport_isolated=True,
            network_request_started=True, network_worker_reaped=True)
        self.task['ledger']['dispatches'].append(event)
        _record_current_acquisition(self.task, self.source, event)

    def test_zero_and_negative_values_are_received_not_missing_or_financially_approved(self):
        original = fingerprint(self.task)
        coverage = collection_coverage(self.task)[0]
        self.assertEqual(coverage['row_count'], 2)
        self.assertEqual([(row['received'], row['missing']) for row in coverage['fields']], [(1, 1), (2, 0)])
        self.assertFalse(coverage['financial_values_approved'] or coverage['full_universe_verified'])
        self.assertNotIn('-987654321', str(coverage))
        self.assertEqual(fingerprint(self.task), original)

    def test_imported_modified_or_other_task_responses_do_not_gain_receipt_coverage(self):
        for modification in ('imported', 'snapshot', 'task'):
            task = copy.deepcopy(self.task); source = task['source_records'][0]
            if modification == 'imported': source.pop('acquisition')
            if modification == 'snapshot': source['snapshot']['tables'][0]['table']['股票代码'][0] = '000001.SZ'
            if modification == 'task': source['task_id'] = 'another-task'
            with self.subTest(modification=modification): self.assertEqual(collection_coverage(task), [])

    def test_forged_summary_counts_cannot_replace_the_actual_response(self):
        self.source['financial_query_contract'] = dict(row_count=999, numeric_evidence_registered=True)
        self.assertEqual(collection_coverage(self.task)[0]['row_count'], 2)

    def test_frozen_report_distinguishes_collection_from_financial_adoption(self):
        self.task.update(status='部分完成', gaps=['金额口径及行业覆盖尚未核实'])
        self.task['questions'][0]['status'] = '未回答'
        self.agent._adopt_method_knowledge(self.task)
        report = compose(self.task)
        self.assertEqual(report['collection_coverage'][0]['row_count'], 2)
        visible = ''.join(MainPaper(render(report, Path(os.environ.get('RESEARCHHELPER_FIXTURE_RESOURCES', ROOT)))).text)
        self.assertIn('资料取得情况', visible)
        self.assertIn('不参与金额比较', visible)
        self.assertIn(self.task['question'], visible)
        self.assertIn('已取得资料：iFinD', visible)
        self.assertNotIn('尚无已核验来源', visible)
        self.assertNotIn('-987654321', visible)
        self.assertEqual(report['evidence'], [])

    def test_changed_response_hash_does_not_repair_original_acquisition_receipt(self):
        self.source['snapshot']['tables'][0]['table'].pop('营业收入[20260630]')
        self.source['hash'] = fingerprint(self.source['snapshot'])
        self.assertEqual(collection_coverage(self.task), [])  # Original receipt no longer binds it.

    def test_other_originals_are_not_rehashed_for_unrelated_collection_display(self):
        task = copy.deepcopy(self.task)
        task['source_records'] = [dict(source='ifind', operation='filing_original', id='other-original')]
        with patch('researchhelper.provenance.query_reference', side_effect=AssertionError('Unrelated original read')):
            self.assertEqual(collection_coverage(task), [])


def accept_packaged(kind='skill'):
    """Check installed Skill or App code without source-engine fallback."""
    import hashlib
    import json
    import sys
    from tests.accept_delivered_ifind_no_data import run_packaged_tests
    sys.path.insert(0, str(ROOT / 'packaging'))
    from build_current import verify_current_build
    from skill.install_project_skill import verify_installation
    selected = Path(os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve(strict=True)
    candidate = verify_current_build(selected, ROOT)
    if kind == 'skill':
        installation = ROOT / '.skills/researchhelper'
        verify = lambda: verify_installation(installation)
        before = verify()
        assert before == hashlib.sha256((selected.parent / 'skill-manifest.json').read_bytes()).hexdigest()
        resources = installation
    elif kind == 'app':
        sys.path.insert(0, str(ROOT / 'packaging/app/macos'))
        from install_app import recorded_manifest, verify_bundle, signature
        bundle = Path('/Applications/ResearchHelper.app')
        manifest = recorded_manifest(selected)
        verify = lambda: verify_bundle(bundle, manifest)
        before = verify(); signature(bundle)
        resources = bundle / 'Contents/Resources'
        installation = resources / 'backend/researchhelper-server/researchhelper-server'
    else:
        raise ValueError('Explicit installed product required')
    output = Path(tempfile.mkdtemp(prefix='current-partial-paper-', dir=ROOT / 'result/acceptance'))
    record = dict(status='failed', candidate=str(selected), network_calls=0, model_calls=0,
        production_store_touched=False, reader_version='rh-reader-13', kind=kind)
    previous = os.environ.get('RESEARCHHELPER_FIXTURE_RESOURCES')
    previous_fixtures = os.environ.get('RESEARCHHELPER_PARTIAL_FIXTURES')
    try:
        os.environ['RESEARCHHELPER_FIXTURE_RESOURCES'] = str(resources)
        modules, expected = ['test_reviewed_partial_answer', 'test_partial_report_collection'], 12
        if kind == 'app':
            closed = ROOT / 'result/acceptance/one-sentence-research-tm5y1fad/task.json'
            closed_task = json.loads(closed.read_text())
            assert closed_task['status'] == '部分完成' and not closed_task['evidence']
            (output / 'closed-task.json').write_text(json.dumps(closed_task, ensure_ascii=False))
            fixture = partial.ReviewedPartialAnswerTests()
            try:
                fixture.setUp()
                reviewed = fixture.reviewed()
                (output / 'synthetic-partial.json').write_text(json.dumps(reviewed, ensure_ascii=False))
            finally:
                fixture.doCleanups()
            os.environ['RESEARCHHELPER_PARTIAL_FIXTURES'] = str(output)
            modules, expected = ['current_partial_app_fixture'], 4
        checked = run_packaged_tests(kind, installation, ROOT, output,
            modules, expected)
        assert verify() == before
        record.update(status='scoped_passed', checks=checked['tests_run'],
            module_paths=checked['module_paths'], installation_unchanged=True)
    except Exception as error:
        record['error_type'] = type(error).__name__
    finally:
        if previous is None: os.environ.pop('RESEARCHHELPER_FIXTURE_RESOURCES', None)
        else: os.environ['RESEARCHHELPER_FIXTURE_RESOURCES'] = previous
        if previous_fixtures is None: os.environ.pop('RESEARCHHELPER_PARTIAL_FIXTURES', None)
        else: os.environ['RESEARCHHELPER_PARTIAL_FIXTURES'] = previous_fixtures
        receipt = output / 'receipt.json'
        receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(dict(status=record['status'], checks=record.get('checks'), receipt=str(receipt))))
    if record['status'] != 'scoped_passed': raise SystemExit(1)
    return receipt


if __name__ == '__main__': unittest.main()
