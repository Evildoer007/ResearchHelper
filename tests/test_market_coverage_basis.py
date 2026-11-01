"""Technical calendar coverage on immutable copies, not new research approval."""
import copy
import json
import tempfile
import unittest
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint, missing_evidence_requirements
from researchhelper.engine import ResearchAgent
from researchhelper.market_calendar import BASIS_POLICY, NEED_SCOPE, covered_need_ids, scope_basis
from researchhelper.planning import input_fingerprint, normalize_questions, question_specs
from researchhelper.reasoning import analysis_contract
from researchhelper.argument_support import review_request, review_messages
from researchhelper.output_schema import planning_output_schema
from researchhelper.reports import compose

SOURCE = ROOT / 'result/acceptance/one-sentence-research-2v83rmi9/task.json'


class MarketCoverageBasisTests(unittest.TestCase):
    def setUp(self):
        self.original = SOURCE.read_bytes()
        self.task = json.loads(self.original)
        self.task[BASIS_POLICY] = 1
        self.question = self.task['questions'][0]
        self.calendar_need = self.question['evidence_needs'][1]
        self.question['evidence_need_scopes'] = {self.calendar_need: NEED_SCOPE}
        self.task['input_hash'] = input_fingerprint(self.task)

    def tearDown(self):
        self.assertEqual(SOURCE.read_bytes(), self.original)

    def test_explicit_calendar_need_uses_actual_query_source_and_original_dates(self):
        basis = scope_basis(self.task)
        self.assertFalse(basis['financial_approval'])
        self.assertEqual(basis['covered_need_ids']['q1'], [fingerprint(self.calendar_need)])
        panel = basis['panels'][0]
        self.assertEqual(panel['query_source']['source'], 'ifind')
        self.assertEqual(panel['calendar_source']['operation'], 'get_trade_dates')
        self.assertEqual(len(panel['calendar_original_dates']), 21)
        self.assertEqual(panel['calendar_original_dates'], panel['calendar_dates'])

    def test_no_keyword_inference_for_untyped_need(self):
        self.question.pop('evidence_need_scopes')
        self.assertEqual(covered_need_ids(self.task, self.question), [])
        self.assertTrue(scope_basis(self.task)['panels'])

    def test_calendar_metadata_cannot_cover_price_need(self):
        missing = missing_evidence_requirements(self.question, [],
            market_need_ids=covered_need_ids(self.task, self.question))
        self.assertEqual([item['text'] for item in missing], [self.question['evidence_needs'][0]])

    def test_price_excerpt_cannot_claim_typed_calendar_requirement(self):
        rows = copy.deepcopy(self.task['evidence'])
        for item in rows:
            item['applicability_review']['evidence_need_ids'] = {'q1': [fingerprint(self.calendar_need)]}
        missing = missing_evidence_requirements(self.question, rows)
        self.assertIn(self.calendar_need, [item['text'] for item in missing])

    def test_absent_changed_or_foreign_calendar_never_covers_need(self):
        for mutation in ('absent', 'hash', 'foreign'):
            task = copy.deepcopy(self.task)
            record = next(item for item in task['source_records'] if item['operation'] == 'get_trade_dates')
            if mutation == 'absent': task['source_records'].remove(record)
            elif mutation == 'hash': record['hash'] = 'changed'
            else: record['acquisition']['task_id'] = 'foreign'
            with self.subTest(mutation=mutation):
                self.assertEqual(covered_need_ids(task, task['questions'][0]), [])

    def test_other_question_cannot_borrow_this_calendar_need(self):
        question = copy.deepcopy(self.question); question['id'] = 'other_question'
        self.assertEqual(covered_need_ids(self.task, question), [])

    def test_old_or_boolean_policy_cannot_activate_new_basis(self):
        for version in (None, False, True, 0, 2):
            self.assertIsNone(scope_basis(dict(self.task, **{BASIS_POLICY: version})))

    def test_new_basis_is_bound_to_analysis_execution_and_review_scope(self):
        # A view-only feature projection, like the date-role unit checks. Do
        # not reclassify the closed study's needs and reuse its old approvals.
        task = json.loads(self.original)
        task[BASIS_POLICY] = 1
        analysis = analysis_contract(task)
        basis = scope_basis(task)
        self.assertEqual(analysis['market_coverage_scope_basis'], basis)
        self.assertEqual(analysis['execution']['market_coverage_basis_hash'], fingerprint(basis))
        proposal = task['argument_support_reviews'][-1]['proposal']
        request = review_request(task, proposal)
        self.assertEqual(request['scope']['market_coverage_scope_basis'], basis)
        self.assertIn('get_trade_dates', json.dumps(review_messages(request, use_reference_units=True)))

    def test_new_basis_does_not_rewrite_prices_calculations_or_return_calendar_approval(self):
        before = copy.deepcopy((self.task['evidence'], self.task['calculations']))
        basis = scope_basis(self.task)
        self.assertEqual((self.task['evidence'], self.task['calculations']), before)
        self.assertFalse(basis['financial_approval'])
        self.assertFalse(basis['panels'][0]['historical_availability_approved'])

    def test_typed_scope_normalization_stays_fact_only_and_exact(self):
        question = question_specs(self.task)[0]
        normalize_questions([question], self.task['question'], self.task['intent'])
        for changes in ({'kind': 'investment'}, {'evidence_need_scopes': {self.calendar_need: 'unknown_scope'}}):
            with self.assertRaises(ContractError):
                normalize_questions([dict(question, **changes)], self.task['question'], self.task['intent'])

    def test_strict_planning_exposes_scope_only_in_new_version(self):
        self.assertIn(NEED_SCOPE, json.dumps(planning_output_schema(self.task)))
        legacy = dict(self.task); legacy.pop(BASIS_POLICY)
        # Existing questions may retain their own explicit need text; the new
        # scope enum itself must not appear in the legacy plan's choices.
        schema = planning_output_schema(legacy)
        def enums(value):
            if isinstance(value, dict):
                return [value.get('enum', [])] + [item for child in value.values() for item in enums(child)]
            if isinstance(value, list): return [item for child in value for item in enums(child)]
            return []
        self.assertFalse(any(NEED_SCOPE in values for values in enums(schema)))

    def test_new_policy_is_hashed_and_frozen_without_upgrading_old_study(self):
        old = dict(self.task); old.pop(BASIS_POLICY)
        self.assertNotEqual(input_fingerprint(old), input_fingerprint(self.task))
        with tempfile.TemporaryDirectory(dir=ROOT / 'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task = agent.create('解释价格变化', questions=[dict(id='q1', kind='fact', question='解释价格变化',
                answer_scope='general_definition', knowledge_needs=['rh_k_price_return_measures'])])
            self.assertEqual(task[BASIS_POLICY], 1)
            content = compose(agent.start(task['id']))
            self.assertEqual(content[BASIS_POLICY], 1)
            self.assertEqual(content['market_coverage_scope_basis']['version'], 1)
            self.assertNotIn(BASIS_POLICY, agent.create_case('mechanism'))
        self.assertNotIn(BASIS_POLICY, json.loads(self.original))


if __name__ == '__main__':
    unittest.main()
