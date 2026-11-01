"""Independent re-extraction and counterexamples on copies of saved studies."""
import copy
import hashlib
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import ContractError, validate_evidence
from researchhelper.evidence import candidate_evidence


STUDIES = ('one-sentence-research-f6e_kjx1', 'one-sentence-research-aq8vm90m',
           'one-sentence-research-ckhviipf', 'one-sentence-research-r_5_wxfy')


def read_study(name):
    return json.loads((ROOT / 'result/acceptance' / name / 'task.json').read_text())


class RetainedExtractionAuditTests(unittest.TestCase):
    def setUp(self):
        self.hashes = {name: hashlib.sha256((ROOT / 'result/acceptance' / name / 'task.json').read_bytes()).hexdigest()
                       for name in STUDIES}
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No external network'))
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self):
        for name, digest in self.hashes.items():
            self.assertEqual(hashlib.sha256((ROOT / 'result/acceptance' / name / 'task.json').read_bytes()).hexdigest(), digest)

    def test_all_twelve_stock_excerpts_still_pass_same_original_validation(self):
        task = read_study(STUDIES[0])
        self.assertEqual(len(task['evidence']), 12)
        for item in task['evidence']:
            validate_evidence(item, task)

    def test_all_four_fee_excerpts_still_pass_same_original_validation(self):
        task = read_study(STUDIES[1])
        self.assertEqual(len(task['evidence']), 4)
        for item in task['evidence']:
            validate_evidence(item, task)

    def test_all_twenty_one_prices_match_exact_returned_cells(self):
        task = read_study(STUDIES[2])
        expected, actual = {}, {}
        for source in task['source_records']:
            if source['operation'] != 'cmd_history_quotation':
                continue
            for table_index, table in enumerate(source['snapshot']['tables']):
                self.assertEqual(table['thscode'], '510500.SH')
                self.assertEqual(len(table['time']), len(table['table']['close']))
                for row, (day, value) in enumerate(zip(table['time'], table['table']['close'])):
                    expected[(source['id'], table_index, row)] = (day, value)
        for item in task['evidence']:
            validate_evidence(item, task)
            locator = item['record_locator']
            key = (item['artifact_id'], locator['table'], locator['row'])
            self.assertEqual(locator['field'], 'close')
            self.assertNotIn(key, actual)
            actual[key] = (item['period'], item['value'])
        self.assertEqual(len(actual), 21)
        self.assertEqual(actual, expected)

    def test_changed_values_units_dates_and_cross_task_are_rejected(self):
        task = read_study(STUDIES[2])
        original = task['evidence'][0]
        for field, value in (('value', 999), ('unit', '美元'), ('period', '2026-08-04'), ('task_id', 'foreign')):
            with self.subTest(field=field):
                item = copy.deepcopy(original)
                item[field] = value
                with self.assertRaises(ContractError):
                    validate_evidence(item, task)

    def test_actual_fomc_proposals_fail_publication_not_download_or_analysis(self):
        task = read_study(STUDIES[3])
        self.assertEqual(task['status'], '部分完成')
        self.assertEqual(len(task['host_result']['evidence_specs']), 4)
        records = {item['id']: item for item in task['source_records']}
        for proposal in task['host_result']['evidence_specs']:
            self.assertTrue(proposal['published_at'])
            self.assertFalse(proposal.get('availability'))
            with self.assertRaisesRegex(ContractError, '发布或披露日期'):
                candidate_evidence(task, records[proposal['artifact_id']], copy.deepcopy(proposal))
        self.assertFalse(task['evidence'])
        self.assertFalse(task.get('argument_support_reviews', []))


def run():
    folder = Path(tempfile.mkdtemp(prefix='retained-extraction-audit-', dir=ROOT / 'result/acceptance'))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(RetainedExtractionAuditTests))
    (folder / 'checks.log').write_text(stream.getvalue())
    receipt = dict(status='scoped_passed' if result.wasSuccessful() else 'failed',
        tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
        external_calls=0, studies_reopened=False, production_store_accessed=False,
        original_authenticity_renewed=False, all_task_types_live_accepted=False,
        test_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    target = folder / 'receipt.json'
    target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(stream.getvalue(), flush=True)
    print(str(target), flush=True)
    return target
