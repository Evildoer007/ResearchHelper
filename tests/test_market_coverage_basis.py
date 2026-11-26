"""Technical calendar coverage on immutable copies, not new research approval."""
import copy
import json
import tempfile
import unittest
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint, missing_evidence_requirements
from researchhelper.engine import ResearchAgent
from researchhelper.market_calendar import BASIS_POLICY, FUNCTION, NEED_SCOPE, covered_need_ids, scope_basis
from researchhelper.planning import input_fingerprint, normalize_questions, question_specs
from researchhelper.reasoning import analysis_contract
from researchhelper.argument_support import review_request, review_messages
from researchhelper.output_schema import planning_output_schema
from researchhelper.reports import compose
from tests.test_market_calendar_coverage import append_calendar_fixture_record


class MarketCoverageBasisTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='calendar-scope-')
        self.addCleanup(folder.cleanup)
        agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True, require_argument_support=True)
        self.addCleanup(agent.close_runtime)
        question = '核对600519.SH区间收盘价及交易日覆盖'
        self.task = agent.create(question, entry='个股', objects=['600519.SH'], research_mode='current',
            questions=[dict(id='q1', kind='fact', question=question,
                evidence_needs=['实际收盘价', '独立交易日历核对报价日期覆盖'])])
        self.task.update(synthetic=True, knowledge=dict(entries=[]))
        self.task.pop(BASIS_POLICY)
        self.task['input_hash'] = input_fingerprint(self.task)
        self.days = ['2026-08-03','2026-08-04','2026-08-05','2026-08-06','2026-08-07','2026-08-10']
        dates = dict(startdate=self.days[0], enddate=self.days[-1])
        append_calendar_fixture_record(self.task, 'cmd_history_quotation', dict(**dates, codes='600519.SH',
            indicators='close', functionpara=dict(Currency='RMB', CPS='1', Fill='Omit')),
            dict(errorcode=0, tables=[dict(thscode='600519.SH',time=self.days,
                table=dict(close=[100,105,110,115,120,117]))]))
        append_calendar_fixture_record(self.task, 'get_trade_dates',
            dict(**dates, marketcode='212001', functionpara=copy.deepcopy(FUNCTION)),
            dict(errorcode=0, tables=dict(time=self.days)))
        self.original = json.dumps(self.task)
        self.task[BASIS_POLICY] = 1
        self.question = self.task['questions'][0]
        self.calendar_need = self.question['evidence_needs'][1]
        self.question['evidence_need_scopes'] = {self.calendar_need: NEED_SCOPE}
        self.task['input_hash'] = input_fingerprint(self.task)

    def tearDown(self):
        self.assertEqual(self.task['source_records'], json.loads(self.original)['source_records'])

    def test_explicit_calendar_need_uses_actual_query_source_and_original_dates(self):
        basis = scope_basis(self.task)
        self.assertFalse(basis['financial_approval'])
        self.assertEqual(basis['covered_need_ids']['q1'], [fingerprint(self.calendar_need)])
        panel = basis['panels'][0]
        self.assertEqual(panel['query_source']['source'], 'ifind')
        self.assertEqual(panel['calendar_source']['operation'], 'get_trade_dates')
        self.assertEqual(panel['calendar_original_dates'], self.days)
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
        rows = [dict(question_ids=['q1'], text='收盘价117元', value=117,
            applicability_review=dict(evidence_need_ids={'q1': [fingerprint(self.calendar_need)]}))]
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
        task['input_hash'] = input_fingerprint(task)
        analysis = analysis_contract(task)
        basis = scope_basis(task)
        self.assertEqual(analysis['market_coverage_scope_basis'], basis)
        self.assertEqual(analysis['execution']['market_coverage_basis_hash'], fingerprint(basis))
        proposal = dict(answers={'q1':dict(answer='按独立交易日历核对报价日期。', refs=[])}, theses=[])
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
