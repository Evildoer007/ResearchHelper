"""Resolve obsolete state, not missing facts, methods or financial approval."""
import copy
import json
import unittest

from tests import ROOT
from researchhelper.engine import ResearchAgent


RETAINED = ROOT / 'result/acceptance/one-sentence-research-wyh3lh1k/task.json'
POLICY = 'evidence_gap_lifecycle_version'


class EvidenceGapLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.original = RETAINED.read_bytes()
        self.task = json.loads(self.original)
        self.task[POLICY] = 1
        self.task.update(gaps=[], gap_records=[], resolved_gap_records=[], optional_findings=[])
        self.agent = ResearchAgent.__new__(ResearchAgent)
        self.ledger = copy.deepcopy(self.task['ledger'])
        self.questions = copy.deepcopy(self.task['questions'])

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(), self.original)
        self.assertEqual(self.task['ledger'], self.ledger)
        self.assertEqual(self.task['questions'], self.questions)

    def gap(self, message, category):
        ResearchAgent._gap(self.task, message, ['q1'], category=category)

    def test_valid_related_evidence_retires_only_the_absence_claim(self):
        self.gap('尚未取得任何依据', 'evidence_absence')
        self.gap('必要计算仍未完成', 'calculation')
        self.agent._resolve_current_gaps(self.task)
        self.assertEqual(self.task['gaps'], ['必要计算仍未完成'])
        self.assertEqual(len(self.task['resolved_gap_records']), 1)
        self.assertEqual(len(self.task['evidence']), 4)
        self.assertFalse(self.task['calculations'])

    def test_bare_flags_foreign_or_changed_evidence_do_not_resolve_absence(self):
        original = copy.deepcopy(self.task['evidence'])
        for evidence in ([], [dict(original[0], task_id='foreign')],
                         [dict(original[0], source_check='待核验')],
                         [dict(original[0], text='Changed original excerpt')]):
            self.task['evidence'] = evidence
            self.task.update(gaps=[], gap_records=[], resolved_gap_records=[])
            self.gap('尚未取得任何依据', 'evidence_absence')
            self.agent._resolve_current_gaps(self.task)
            self.assertEqual(self.task['gaps'], ['尚未取得任何依据'])

    def test_legacy_unknown_and_foreign_gap_records_are_not_reclassified(self):
        self.task.pop(POLICY)
        self.gap('尚未取得任何依据', 'evidence_absence')
        self.agent._resolve_current_gaps(self.task)
        self.assertEqual(self.task['gaps'], ['尚未取得任何依据'])
        self.task[POLICY] = 1
        self.task['gap_records'][0]['task_id'] = 'foreign'
        self.agent._resolve_current_gaps(self.task)
        self.assertEqual(self.task['gaps'], ['尚未取得任何依据'])

    def test_latest_same_question_coverage_supersedes_old_detail_but_stays_blocking(self):
        self.gap('缺少价格、净值和份额', 'question_coverage')
        self.gap('仅缺少价格', 'question_coverage')
        self.assertEqual(self.task['gaps'], ['仅缺少价格'])
        self.assertEqual(self.task['resolved_gap_records'][0]['message'], '缺少价格、净值和份额')
        self.assertTrue(self.task['gap_records'][0]['required'])

    def test_untyped_and_other_question_history_is_not_hidden(self):
        self.gap('独立来源仍缺失', None)
        self.gap('旧覆盖描述', 'question_coverage')
        record = copy.deepcopy(self.task['gap_records'][-1])
        record.update(question_ids=['another_question'], message='其他问题缺口')
        self.task['gap_records'].append(record); self.task['gaps'].append(record['message'])
        self.gap('新覆盖描述', 'question_coverage')
        self.assertEqual(self.task['gaps'], ['独立来源仍缺失', '其他问题缺口', '新覆盖描述'])

    def extraction_state(self):
        self.task['reasoning_state']['extraction'].update(question_ids=['q1'], accepted_dispatch=10)

    def test_later_accepted_extraction_resolves_format_gap_not_core_question(self):
        self.gap('旧提取未完成', 'evidence_extraction')
        self.task['gap_records'][-1]['failure_dispatch'] = 8
        self.extraction_state()
        self.gap('核心问题缺少价格', 'question_coverage')
        self.agent._resolve_current_gaps(self.task)
        self.assertEqual(self.task['gaps'], ['核心问题缺少价格'])
        self.assertFalse(self.task['calculations'])

    def test_old_success_wrong_binding_or_unknown_dispatch_cannot_resolve_new_failure(self):
        self.gap('新提取未完成', 'evidence_extraction')
        self.extraction_state()
        original = copy.deepcopy(self.task['reasoning_state']['extraction'])
        for change in ({}, dict(accepted_dispatch=7), dict(question_ids=['foreign'])):
            self.task['reasoning_state']['extraction'] = dict(copy.deepcopy(original), **change)
            self.agent._resolve_current_gaps(self.task)
            self.assertEqual(self.task['gaps'], ['新提取未完成'])
        self.task['gap_records'][-1]['failure_dispatch'] = 8
        self.task['reasoning_state']['extraction'] = copy.deepcopy(original)
        self.task['reasoning_state']['extraction']['receipt']['task_id'] = 'foreign'
        self.agent._resolve_current_gaps(self.task)
        self.assertEqual(self.task['gaps'], ['新提取未完成'])

    def test_repeated_resolution_is_idempotent(self):
        self.gap('没有依据', 'evidence_absence')
        self.agent._resolve_current_gaps(self.task)
        first = copy.deepcopy(self.task)
        self.agent._resolve_current_gaps(self.task)
        self.assertEqual(self.task, first)


if __name__ == '__main__':
    unittest.main()
