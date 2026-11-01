"""Knowledge explanation does not grant facts, numbers, judgment or tool access."""

import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.planning import normalize_questions, plan_preservation_contract, question_specs
from researchhelper.reasoning import analysis_contract, analysis_numeric_review, validate_analysis_output, validate_model_specs, validate_plan
from researchhelper.reports import compose

ROOT = Path(__file__).resolve().parents[2]
DEFINITION = {'id': 'q1', 'kind': 'fact', 'question': '解释ETF价格、净值和总回报的通用口径，不查询实际基金值。',
              'answer_scope': 'general_definition', 'knowledge_needs': ['rh_k_etf_measures']}


class GeneralDefinitionAnswersTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='definition-', dir=ROOT / 'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)
        guard = patch('socket.socket.connect', side_effect=AssertionError('No network for definitions'))
        guard.start(); self.addCleanup(guard.stop)

    def create(self, questions=None):
        return self.agent.create(DEFINITION['question'], entry='ETF', questions=questions or [copy.deepcopy(DEFINITION)])

    def test_standalone_definition_is_saved_without_model_data_or_thesis(self):
        task = self.create()
        done = self.agent.start(task['id'])
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertFalse(done['evidence'])
        self.assertFalse(done['calculations'])
        self.assertFalse(done['theses'])
        answer = done['questions'][0]
        self.assertEqual(answer['status'], '已回答')
        self.assertFalse(answer['knowledge_integrity']['fact_approval'])
        self.assertIn('不代表当前基金事实', answer['answer'])
        self.assertIn('适用条件', answer['answer'])
        self.assertIn('限制', answer['answer'])
        self.assertEqual(done['ledger']['used']['data_requests'], 0)
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertTrue(done['delivery'])
        self.assertEqual(self.agent.archive.get(done['id']), done)
        self.assertEqual(compose(done)['questions'][0]['answer'], answer['answer'])

    def test_definition_does_not_satisfy_a_missing_fact_branch(self):
        questions = [{'id': 'fact', 'question': '查询510300.SH实际净值', 'kind': 'fact',
                      'evidence_needs': ['对应基金同估值时点官方净值']}, dict(DEFINITION, id='definition')]
        task = self.create(questions)
        done = self.agent.start(task['id'])
        self.assertEqual(done['status'], '部分完成', done.get('error'))
        self.assertEqual(done['questions'][0]['status'], '未回答')
        self.assertEqual(done['questions'][1]['status'], '已回答')
        self.assertFalse(done['evidence'])
        self.assertTrue(done['delivery'])

    def test_fact_comparison_and_mechanism_cannot_use_the_scope_to_skip_requirements(self):
        for extra in ({'kind': 'mechanism'}, {'kind': 'investment'}, {'kind': 'comparison'},
                      {'evidence_needs': ['净值']}, {'dependencies': ['q2']},
                      {'method_needs': ['observed_price_return_v1']}, {'knowledge_needs': []}):
            with self.subTest(extra=extra), self.assertRaises(ContractError):
                normalize_questions([dict(DEFINITION, **extra)], DEFINITION['question'], '综合研究')

    def test_adopted_fact_cannot_be_downgraded_to_definition(self):
        question = {'id': 'q1', 'question': DEFINITION['question'], 'kind': 'fact', 'evidence_needs': ['实际基金原件']}
        task = self.create([question])
        with self.assertRaises(ContractError):
            self.agent.revise_plan(task['id'], [DEFINITION], '不能绕过事实', task['input_hash'])
        self.assertEqual(self.agent.archive.get(task['id']), task)

    def test_arbitrary_model_claim_or_number_is_rejected_not_rewritten(self):
        task = self.create()
        refs = [{'id': 'rh_k_etf_measures', 'version': 2}]
        for extra in ({'answer': '该基金净值上涨0.3623%。'}, {'refs': ['made_up_fact']},
                      {'calculation_refs': ['made_up_calc']}, {'numeric_segments': {'answer': [{'text': '买入'}]}}):
            proposal = dict(knowledge_refs=refs, **extra)
            before = copy.deepcopy(proposal)
            with self.subTest(extra=extra), self.assertRaises(ContractError):
                self.agent.knowledge.definition_answer(task, task['questions'][0], proposal)
            self.assertEqual(proposal, before)
        self.assertFalse(analysis_numeric_review(task, {'answers': {'q1': dict(knowledge_refs=refs, answer='买入')}, 'theses': []})['accepted'])

    def test_host_data_and_calculation_request_blocked_before_spending(self):
        task = self.create()
        for field in ('data_requests', 'calculations'):
            with self.subTest(field=field), self.assertRaises(ContractError):
                self.agent.start(task['id'], {field: [{'question_ids': ['q1']}]})
            self.assertEqual(self.agent.archive.get(task['id']), task)

    def test_report_rechecks_answer_receipt_and_entry_version(self):
        done = self.agent.start(self.create()['id'])
        self.assertEqual(done['status'], '已完成', done.get('error'))
        for change in ('answer', 'receipt', 'version', 'body'):
            edited = copy.deepcopy(done)
            if change == 'answer':
                edited['questions'][0]['answer'] += '该基金一定上涨。'
            elif change == 'receipt':
                edited['questions'][0]['knowledge_integrity']['task_id'] = 'other-task'
            elif change == 'version':
                edited['questions'][0]['knowledge_refs'][0]['version'] = 1
            else:
                next(e for e in edited['knowledge']['entries'] if e['id'] == 'rh_k_etf_measures')['content'] = '伪造事实'
            with self.subTest(change=change), self.assertRaises(ContractError):
                compose(edited)

    def test_ordinary_questions_keep_their_old_shape_and_hash_inputs(self):
        value = normalize_questions([{'id': 'q1', 'question': '何时生效', 'kind': 'fact'}], '何时生效', '综合研究')[0]
        self.assertEqual(set(value), {'id', 'question', 'kind', 'required', 'group', 'dependencies', 'evidence_needs', 'method_needs'})
        self.assertNotIn('answer_scope', value)
        task = self.agent.create('何时生效', questions=[value])
        preserved = plan_preservation_contract(task)['protected_questions'][0]
        self.assertNotIn('answer_scope', preserved['exact_fields'])
        self.assertNotIn('knowledge_needs', preserved['retained_list_items'])

    def test_shared_analysis_contract_exposes_exact_definition_refs(self):
        task = self.create()
        pending = analysis_contract(task)['by_question']['q1']['definition']
        self.assertFalse(pending['generation_ready'])
        self.assertEqual(pending['knowledge_refs'], [])
        task = self.agent.start(task['id'])
        before = fingerprint(task)
        contract = analysis_contract(task)['by_question']['q1']['definition']
        self.assertEqual(contract['knowledge_refs'], [{'id': 'rh_k_etf_measures', 'version': 2}])
        self.assertFalse(contract['free_prose'])
        self.assertFalse(contract['fact_approval'])
        self.assertEqual(fingerprint(task), before)
        self.assertEqual(question_specs(task)[0]['knowledge_needs'], ['rh_k_etf_measures'])

    def test_skill_reads_the_same_pending_and_adopted_contract(self):
        spec = importlib.util.spec_from_file_location('definition_skill', ROOT / 'core/session.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        session = module.Session.__new__(module.Session)
        session.archive = self.agent.archive
        task = self.create()
        self.assertFalse(session.tools(task['id'])['analysis']['by_question']['q1']['definition']['generation_ready'])
        done = self.agent.start(task['id'])
        self.assertEqual(session.tools(task['id'])['analysis'], analysis_contract(done))
        self.assertEqual(self.agent.export(done['id'])['content']['questions'][0]['knowledge_integrity'],
                         done['questions'][0]['knowledge_integrity'])

    def test_model_cannot_extract_a_fact_or_propose_a_tool_for_definition(self):
        task = self.create()
        with self.assertRaises(ContractError):
            validate_model_specs(task, [{'question_ids': ['q1']}], schema_only=True)
        with self.assertRaises(ContractError):
            validate_plan(task, {'questions': [DEFINITION], 'data_requests': [], 'calculations': [
                {'id': 'wrong', 'method': 'observed_price_return_v1', 'question_ids': ['q1']}]})
        with self.assertRaises(ContractError):
            self.agent.start(task['id'], {'theses': [{'question_ids': ['q1'], 'judgment': '支持'}]})
        self.assertEqual(self.agent.archive.get(task['id']), task)

    def test_false_or_other_version_is_not_an_alias_for_version_one(self):
        done = self.agent.start(self.create()['id'])
        for version in (True, 1, 3, '2'):
            proposal = {'knowledge_refs': [{'id': 'rh_k_etf_measures', 'version': version}]}
            with self.subTest(version=version), self.assertRaises(ContractError):
                self.agent.knowledge.definition_answer(done, done['questions'][0], proposal)
        with self.assertRaises(ContractError):
            validate_analysis_output(None, done, {'answers': {'q1': {
                'knowledge_refs': [{'id': 'rh_k_etf_measures', 'version': True}]}}, 'theses': []}, 0)

    def test_unpublished_or_wrong_role_cannot_be_used_as_definition(self):
        for ident in ('rh_k_unknown', 'rh_k_price_change'):
            with self.subTest(ident=ident):
                question = dict(DEFINITION, knowledge_needs=[ident])
                task = self.create([question])
                if ident == 'rh_k_unknown':
                    with self.assertRaises(ContractError):
                        self.agent.start(task['id'])
                    done = self.agent.archive.get(task['id'])
                    self.assertEqual(done['ledger']['used']['data_requests'], 0)
                    self.assertEqual(done['ledger']['used']['model_calls'], 0)
                    continue
                done = self.agent.start(task['id'])
                self.assertNotEqual(done['status'], '已完成')
                self.assertEqual(done['ledger']['used']['data_requests'], 0)
                self.assertEqual(done['ledger']['used']['model_calls'], 0)


if __name__ == '__main__':
    unittest.main()
