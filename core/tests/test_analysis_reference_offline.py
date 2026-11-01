"""Per-question analysis references, no model/network or approval shortcuts."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, evidence_requirements, fingerprint
from researchhelper.reasoning import analysis_contract
from tests import test_research_coverage_offline as fixtures

ROOT = Path(__file__).resolve().parents[2]


class AnalysisReferenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResearchCoverageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        network = patch('socket.socket.connect', side_effect=AssertionError('External network forbidden'))
        network.start()
        self.addCleanup(network.stop)

    def complete(self, kind='fact'):
        task = self.fixture.task(kind=kind)
        return self.fixture.result(task, self.fixture.specs(task),
            theses=[self.fixture.thesis()] if kind == 'mechanism' else [])

    def test_contract_is_read_only_bound_and_coverage_uses_actual_refs(self):
        task = self.complete()
        before = fingerprint(task)
        contract = analysis_contract(task)
        self.assertEqual(fingerprint(task), before)
        self.assertEqual(contract['binding']['task_id'], task['id'])
        self.assertEqual(contract['binding']['input_hash'], task['input_hash'])
        self.assertFalse(contract['contract_is_approval'])
        self.assertFalse(contract['automatic_reference_repair'])
        question = contract['by_question']['q1']
        self.assertEqual(question['evidence_ids'], ['effective_date', 'eligibility'])
        self.assertEqual([item['evidence_ids'] for item in question['requirements']],
                         [['effective_date'], ['eligibility']])
        self.assertNotIn('original_base64', json.dumps(contract))

    def test_two_questions_have_distinct_reference_lists(self):
        task = self.fixture.task(questions=[{'id': 'q1', 'question': '日期和范围', 'kind': 'fact',
            'evidence_needs': fixtures.NEEDS}, {'id': 'q2', 'question': '仅日期', 'kind': 'fact',
            'evidence_needs': ['生效日期']}])
        specs = self.fixture.specs(task)
        extra = copy.deepcopy(specs[0])
        extra.update(id='q2_date', question_ids=['q2'])
        extra['review'].update(question_ids=['q2'], evidence_need_ids={
            'q2': [evidence_requirements(task['questions'][1])[0]['id']]})
        done = self.fixture.agent.start(task['id'], {'evidence_specs': specs + [extra], 'answers': {
            'q1': {'answer': '日期和范围见原件', 'refs': ['effective_date', 'eligibility']},
            'q2': {'answer': '日期见原件', 'refs': ['q2_date']}}})
        self.assertEqual(done['status'], '已完成', done.get('error'))
        contract = analysis_contract(done)
        self.assertEqual(contract['by_question']['q1']['evidence_ids'], ['effective_date', 'eligibility'])
        self.assertEqual(contract['by_question']['q2']['evidence_ids'], ['q2_date'])
        self.assertEqual(list(analysis_contract(done, ['q2'])['by_question']), ['q2'])

    def test_unknown_duplicate_and_empty_selected_scope_reject_before_dispatch(self):
        task = self.complete()
        for selected in ([], ['q99'], ['q1', 'q1'], 'q1'):
            with self.subTest(selected=selected), self.assertRaises(ContractError):
                analysis_contract(task, selected)

    def test_tampered_evidence_is_not_an_approved_reference(self):
        task = self.complete()
        task['evidence'][0]['text'] = '伪造摘录'
        with self.assertRaises(ContractError):
            analysis_contract(task)

    def test_no_evidence_does_not_create_reference_ids(self):
        task = self.fixture.task()
        contract = analysis_contract(task)
        self.assertEqual(contract['by_question']['q1']['evidence_ids'], [])
        self.assertTrue(all(not item['evidence_ids'] for item in contract['by_question']['q1']['requirements']))

    def test_app_analyze_receives_same_bound_contract(self):
        task = self.complete()
        class Host:
            def reason(self, task, messages):
                self.messages = messages
                return '{"answers":{},"theses":[]}'
        host = Host()
        self.fixture.agent.model = host
        self.fixture.agent._reason_on_verified_material(task, ['q1'])
        payload = json.loads(host.messages[1]['content'])
        self.assertEqual(payload['analysis_contract'], analysis_contract(task, ['q1']))
        self.assertEqual(task['ledger']['used']['model_calls'], 0)

    def test_skill_tools_expose_same_contract_without_second_model(self):
        task = self.complete()
        spec = importlib.util.spec_from_file_location('reference_skill', ROOT / 'core/session.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        session = module.Session.__new__(module.Session)
        session.archive = self.fixture.agent.archive
        self.assertEqual(session.tools(task['id'])['analysis'], analysis_contract(task))

    def test_prose_counter_is_still_rejected_not_removed(self):
        task = self.fixture.task(kind='mechanism')
        thesis = self.fixture.thesis()
        thesis['counter'] = ['其他机制可能抵消影响']
        done = self.fixture.result(task, self.fixture.specs(task), theses=[thesis])
        self.assertEqual(done['status'], '部分完成')
        self.assertEqual(done['theses'], [])
        self.assertEqual(thesis['counter'], ['其他机制可能抵消影响'])

    def test_counter_empty_with_explicit_limit_is_accepted_without_new_evidence(self):
        done = self.complete(kind='mechanism')
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertEqual(done['theses'][0]['counter'], [])
        self.assertEqual(len(done['evidence']), 2)
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertEqual(done['ledger']['used']['data_requests'], 0)


if __name__ == '__main__':
    unittest.main()
