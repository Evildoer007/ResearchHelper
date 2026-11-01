"""Single-cell independent checks and bounded live guard; fabricated values."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.bootstrap import configure_imports
configure_imports()
from researchhelper.contracts import fingerprint
from researchhelper.knowledge import ENTRIES
from tests import accept_current_live_etf_price_definition as probe


class CurrentETFPriceDefinitionTests(unittest.TestCase):
    def setUp(self):
        knowledge = copy.deepcopy(next(item for item in ENTRIES if item['id'] == 'rh_k_etf_measures' and item['version'] == 2))
        snapshot = {'errorcode': 0, 'tables': [{'thscode': probe.CODE, 'time': [probe.OBSERVATION],
                                               'table': {'close': [10.0]}}]}
        source = {'id': 'raw1', 'task_id': 'fixture', 'research_version': 1, 'source': 'ifind',
                  'operation': 'cmd_history_quotation', 'snapshot': snapshot, 'hash': fingerprint(snapshot),
                  'parameters': {'functionpara': {'Currency': 'RMB', 'CPS': '1', 'Fill': 'Omit'}}}
        evidence = {'id': 'price', 'artifact_id': source['id'], 'task_id': 'fixture', 'research_version': 1,
                    'record_locator': {'table': 0, 'row': 0, 'field': 'close'}, 'snapshot_hash': source['hash'],
                    'value': 10.0, 'raw_value': 10.0, 'raw_unit': '元', 'unit': '元', 'conversion_factor': 1,
                    'source_check': '通过', 'applicability_check': '通过', 'nature': '已实现事实'}
        self.task = {'id': 'fixture', 'version': 1, 'entry': 'ETF', 'question': probe.QUESTION, 'status': '已完成',
                     'source_records': [source], 'evidence': [evidence], 'calculations': [], 'theses': [], 'tracking': [],
                     'knowledge': {'entries': [knowledge]}, 'questions': [
                         {'id': 'q1', 'question': probe.QUESTION, 'required': True, 'status': '已回答',
                          'answer': '合成验收交易价格，不代表单位净值。', 'refs': ['price']},
                         {'id': 'q2', 'question': '通用口径', 'required': True, 'status': '已回答',
                          'answer_scope': 'general_definition', 'answer': knowledge['content'], 'refs': []}]}

    def rejected(self, mutate):
        task = copy.deepcopy(self.task)
        mutate(task)
        with self.assertRaises(AssertionError):
            probe.verify_price_definition(task)

    def test_original_cell_is_checked_without_expected_price_in_prompt(self):
        result = probe.verify_price_definition(self.task)
        self.assertEqual(result['raw_close'], 10.0)
        self.assertFalse(result['independent_investment_quality'])
        self.assertNotIn('10.0', probe.QUESTION)

    def test_changed_value_cannot_hide_behind_approval_flags(self):
        self.rejected(lambda task: task['evidence'][0].update(value=12.0))

    def test_wrong_unit_or_unverified_applicability_is_rejected(self):
        for changes in ({'unit': '点'}, {'raw_unit': '美元'}, {'applicability_check': '待核验'},
                        {'usage_scope': 'document_content'}, {'nature': '预测'}):
            self.rejected(lambda task: task['evidence'][0].update(changes))

    def test_wrong_code_or_date_does_not_count(self):
        for changes in ({'thscode': '510300.SH'}, {'time': ['2026-10-13']}):
            self.rejected(lambda task: task['source_records'][0]['snapshot']['tables'][0].update(changes))

    def test_changed_snapshot_hash_or_cross_task_is_rejected(self):
        self.rejected(lambda task: task['source_records'][0].update(hash='forged'))
        self.rejected(lambda task: task['source_records'][0].update(task_id='other-task'))
        self.rejected(lambda task: task['evidence'][0].update(research_version=2))

    def test_definition_does_not_replace_price_citation(self):
        self.rejected(lambda task: task['questions'][0].update(refs=[]))
        self.rejected(lambda task: task['questions'][1].pop('answer_scope'))

    def test_numeric_calculation_or_thesis_is_outside_this_case(self):
        self.rejected(lambda task: task.update(calculations=[{'method': 'invented_premium'}]))

    def test_no_authorization_or_empty_token_never_reaches_model_settings(self):
        with patch.dict('os.environ', {}, clear=True), self.assertRaises(RuntimeError):
            probe.selected_candidate()
        with patch.object(probe, 'selected_candidate', return_value=Path('/invalid')), self.assertRaises(ValueError):
            probe.run('')

    def test_closed_case_cannot_be_retried_with_a_new_candidate(self):
        folder = tempfile.TemporaryDirectory(prefix='etf-closed-guard-', dir=probe.ROOT / 'result/acceptance')
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        closed = root / 'result/acceptance/live-etf-price-definition-closed'
        closed.mkdir(parents=True)
        (closed / 'receipt.json').write_text(json.dumps({'case': probe.CASE, 'status': 'failed'}))
        with patch.object(probe, 'ROOT', root), patch.dict('os.environ', {
            'RESEARCHHELPER_AUTHORIZED_LIVE_CASE': probe.AUTHORIZATION,
            'RESEARCHHELPER_CURRENT_RECEIPT': str(root / 'different-candidate.json')}, clear=True), self.assertRaises(RuntimeError):
            probe.selected_candidate()


if __name__ == '__main__':
    unittest.main()
