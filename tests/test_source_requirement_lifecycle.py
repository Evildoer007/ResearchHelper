"""Typed provenance and gap checks using verified synthetic market records."""
import copy
from functools import lru_cache
import json
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, Mock

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError, METHOD_REGISTRY, charge, fingerprint, missing_evidence_requirements
from researchhelper.engine import ResearchAgent
from researchhelper.planning import normalize_questions, question_contract, question_specs, validate_question_revision
from researchhelper.provenance import bind_query_references
from researchhelper.reasoning import analysis_contract, evidence_contract, model_object
from researchhelper.evidence import (candidate_evidence, api_cell_choices, expand_api_cell_selection,
                                    snapshot_record, _record_current_acquisition)

PRICE_NEEDS = {
    '2026-09-21': '核对2026-09-21的实际收盘原值',
    '2026-09-30': '核对2026-09-30的实际收盘原值',
}
TRACE_NEED = '核对本次查询来源、操作、参数与响应身份'
METHOD = 'observed_price_return_v1'


@lru_cache(maxsize=1)
def price_task():
    with TemporaryDirectory(prefix='requirement-lifecycle-') as directory, \
            patch('socket.socket.connect', side_effect=AssertionError('offline regression')):
        agent = ResearchAgent(ResearchArchive(directory), ROOT, offline=True)
        try:
            code = '510300.SH'
            question = '核对510300.SH的两日收盘价、区间变化及本次查询记录'
            task = agent.create(question, entry='ETF', objects=[code], research_mode='current',
                questions=[dict(id='q1', question=question, kind='fact', method_needs=[METHOD],
                                evidence_needs=[*PRICE_NEEDS.values(), TRACE_NEED])])
            start, end = PRICE_NEEDS
            parameters = dict(codes=code, indicators='close', startdate=start, enddate=end,
                              functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
            snapshot = dict(errorcode=0, tables=[dict(thscode=code, time=[start, end], table={'close': [4, 4.5]})])
            record = snapshot_record(task, 'ifind', 'cmd_history_quotation', parameters, snapshot)
            charge(task['ledger'], 'data_requests')
            event = dict(kind='data_requests', source='ifind', operation=record['operation'],
                         fingerprint=record['request_hash'], batch=task['batch'], status='成功',
                         transport_isolated=True, network_request_started=True, network_worker_reaped=True)
            task['ledger']['dispatches'].append(event)
            _record_current_acquisition(task, record, event)
            record['question_ids'] = ['q1']
            task['source_records'].append(record)
            specs, references = [], {}
            for choice in api_cell_choices(task, record):
                period = choice['period']
                review = dict(question_ids=['q1'], allowed_natures=['已实现事实'], conditions=['合成的两日收盘原值'],
                    reason='已核对标的、登记单位及交易日', evidence_need_ids={'q1': [fingerprint(PRICE_NEEDS[period])]})
                spec = expand_api_cell_selection(task, dict(api_cell_selection=choice['candidate_id'],
                                                          question_ids=['q1'], review=review))
                evidence = candidate_evidence(task, record, spec)
                task['evidence'].append(ResearchAnalyzer().assess(task, evidence, review, task['source_records']))
                references[period] = evidence['id']
                specs.append(spec)
            task['host_result'] = {'evidence_specs': specs}
            calculation = dict(id='observed_return', method=METHOD, question_ids=['q1'],
                inputs={'start_price': references[start], 'end_price': references[end]},
                parameters=dict(METHOD_REGISTRY[METHOD]['parameters'], start_date=start, end_date=end))
            task['calculation_specs'] = [calculation]
            agent._adopt_method_knowledge(task)
            agent._calculate_spec(task, calculation)
            assert len(task['evidence']) == 2 and len(task['calculations']) == 1, task['gaps']
            return json.dumps(task, ensure_ascii=False)
        finally:
            agent.close_runtime()


class SourceRequirementLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.task = json.loads(price_task())
        self.agent = ResearchAgent.__new__(ResearchAgent)
        guard = patch('socket.socket.connect', side_effect=AssertionError('offline regression'))
        guard.start()
        self.addCleanup(guard.stop)

    def projected_plan(self):
        q = self.task['questions'][0]
        q['evidence_need_scopes'] = {q['evidence_needs'][-1]:'query_provenance'}
        return q

    def clear_gaps(self):
        self.task.update(gaps=[], gap_records=[], resolved_gap_records=[], optional_findings=[])

    def calc_gap(self, text='先前计算未成功'):
        ResearchAgent._gap(self.task, text, ['q1'], category='calculation', method_ids=['observed_price_return_v1'])

    def test_mixed_plan_types_only_metadata_need(self):
        q = self.projected_plan()
        accepted = normalize_questions(question_specs(self.task), self.task['question'], self.task['intent'])[0]
        self.assertEqual(accepted['method_needs'], ['observed_price_return_v1'])
        self.assertEqual(len(accepted['evidence_need_scopes']), 1)
        self.assertEqual(question_contract()['query_provenance']['guidance_version'], 2)

    def test_identifiers_contract_rejects_the_actual_digit_leading_failure(self):
        import re
        contract = evidence_contract(self.task)['identifier_contract']
        self.assertIsNotNone(re.fullmatch(contract['pattern'], contract['example']))
        self.assertIsNone(re.fullmatch(contract['pattern'], '510300_SH_close_20260921'))
        source = self.task['source_records'][0]
        with self.assertRaisesRegex(ContractError, '证据标识'):
            candidate_evidence(self.task, source, {'id':'510300_SH_close_20260921'})
        self.assertFalse(contract['automatic_identifier_repair'])

    def test_adopted_financial_scope_cannot_be_downgraded(self):
        proposed = question_specs(self.task)
        proposed[0]['evidence_need_scopes'] = {proposed[0]['evidence_needs'][-1]:'query_provenance'}
        with self.assertRaises(ContractError):
            validate_question_revision(self.task, proposed)

    def test_explicit_metadata_and_prices_cover_distinct_requirements(self):
        q = self.projected_plan()
        contract = analysis_contract(self.task)['by_question']['q1']['query_provenance']
        option = contract['candidates'][0]['reference_options'][0]
        bound = bind_query_references(self.task, q, [option])
        self.assertEqual(missing_evidence_requirements(q, self.task['evidence'], bound['covered_need_ids']), [])
        self.assertEqual(len(missing_evidence_requirements(q, [], bound['covered_need_ids'])), 2)
        self.assertFalse(bound['receipt']['financial_approval'])

    def test_reference_options_do_not_bind_or_mutate_the_task(self):
        self.projected_plan()
        before = copy.deepcopy(self.task)
        contract = analysis_contract(self.task)['by_question']['q1']['query_provenance']
        self.assertTrue(contract['candidates'][0]['reference_options'])
        self.assertEqual(self.task, before)
        self.assertNotIn('provenance_integrity', self.task['questions'][0])

    def test_response_tampering_does_not_satisfy_metadata(self):
        q = self.projected_plan()
        option = analysis_contract(self.task)['by_question']['q1']['query_provenance']['candidates'][0]['reference_options'][0]
        self.task['source_records'][0]['snapshot']['tables'][0]['table']['close'][0] += 1
        with self.assertRaises(ContractError):
            bind_query_references(self.task, q, [option])

    def test_successful_calculation_resolves_only_its_typed_failure(self):
        self.clear_gaps()
        self.calc_gap()
        ResearchAgent._gap(self.task, '来源关联仍缺失', ['q1'])
        ledger = copy.deepcopy(self.task['ledger'])
        self.agent._resolve_current_gaps(self.task)
        self.assertEqual(self.task['gaps'], ['来源关联仍缺失'])
        archived = self.task['resolved_gap_records'][0]
        self.assertEqual(archived['message'], '先前计算未成功')
        self.assertEqual(archived['resolution']['calculation_ids'], [self.task['calculations'][0]['id']])
        self.assertEqual(self.task['ledger'], ledger)
        self.agent._resolve_current_gaps(self.task)
        self.assertEqual(len(self.task['resolved_gap_records']), 1)

    def test_missing_calculation_stays_unresolved(self):
        self.clear_gaps(); self.calc_gap()
        self.task['calculations'] = []
        self.agent._resolve_current_gaps(self.task)
        self.assertTrue(self.task['gaps'])
        self.assertFalse(self.task['resolved_gap_records'])

    def test_another_task_or_version_calculation_cannot_resolve_gap(self):
        for field,value in [('task_id','other'),('research_version',2),('research_version',True)]:
            with self.subTest(field=field):
                self.clear_gaps(); self.calc_gap()
                calculation = copy.deepcopy(self.task['calculations'][0])
                self.task['calculations'][0][field] = value
                self.agent._resolve_current_gaps(self.task)
                self.assertTrue(self.task['gaps'])
                self.task['calculations'][0] = calculation

    def test_old_or_foreign_typed_gap_is_not_relabelled_resolved(self):
        self.clear_gaps(); self.calc_gap()
        self.task['gap_records'][0]['research_version'] = 0
        self.task['questions'][0]['status'] = '已回答'
        self.agent._resolve_current_gaps(self.task)
        self.assertTrue(self.task['gaps'])

    def test_shared_message_is_retained_for_unresolved_question(self):
        self.clear_gaps()
        second = dict(copy.deepcopy(self.task['questions'][0]), id='q2', status='未回答')
        self.task['questions'].append(second)
        self.task['questions'][0]['status'] = '已回答'
        ResearchAgent._gap(self.task, '核查未完成', ['q1'])
        ResearchAgent._gap(self.task, '核查未完成', ['q2'])
        self.agent._resolve_current_gaps(self.task)
        self.assertEqual(self.task['gaps'], ['核查未完成'])
        self.assertEqual(self.task['gap_records'][0]['question_ids'], ['q2'])

    def test_unknown_error_is_not_hidden_by_matching_words(self):
        self.clear_gaps(); self.calc_gap()
        self.task['gaps'].append('必要计算尚未完成：不明旧记录')
        self.agent._resolve_current_gaps(self.task)
        self.assertEqual(self.task['gaps'], ['必要计算尚未完成：不明旧记录'])

    def test_all_answered_legacy_question_gaps_are_archived_not_erased(self):
        self.clear_gaps()
        ResearchAgent._gap(self.task, '先前问题未回答', ['q1'])
        self.task['questions'][0]['status'] = '已回答'
        self.agent._resolve_current_gaps(self.task)
        self.assertFalse(self.task['gaps'])
        self.assertEqual(self.task['resolved_gap_records'][0]['message'], '先前问题未回答')

    def test_resolved_supplement_stop_keeps_decision_but_not_client_gap(self):
        self.clear_gaps()
        self.agent._stop_supplement(self.task, '本次无适用增量动作')
        decisions = copy.deepcopy(self.task['supplement_decisions'])
        self.task['questions'][0]['status'] = '已回答'
        self.agent._resolve_current_gaps(self.task)
        self.assertFalse(self.task['gaps'])
        self.assertEqual(self.task['supplement_decisions'], decisions)
        self.assertEqual(self.task['resolved_gap_records'][0]['category'], 'supplement_stop')

    def test_supplement_new_evidence_reaches_original_method_binding(self):
        self.clear_gaps()
        self.task['run_mode'] = 'model'
        self.task['calculations'] = []
        self.task['calculation_specs'] = []
        active = {'affected_questions':['q1'], 'before':{'calculations':[]}}
        self.agent._active_supplement = Mock(return_value=active)
        self.agent._bind_initial_calculations = Mock()
        self.agent._step(self.task,'calculation',None)
        self.agent._bind_initial_calculations.assert_called_once_with(self.task,['q1'])
        self.assertTrue(self.task['gaps'])

    def test_calculation_binding_refuses_foreign_question_scope(self):
        with self.assertRaisesRegex(ContractError, '研究问题'):
            self.agent._bind_initial_calculations(self.task,['not_this_task'])

    def selection(self):
        choice = api_cell_choices(self.task,self.task['source_records'][0])[0]
        review = copy.deepcopy(self.task['host_result']['evidence_specs'][0]['review'])
        return choice, {'api_cell_selection':choice['candidate_id'],'question_ids':['q1'],'review':review}

    def test_short_cell_choice_keeps_exact_actual_raw_value_and_normal_checks(self):
        choice,selection = self.selection()
        expanded = expand_api_cell_selection(self.task,selection)
        item = candidate_evidence(self.task,self.task['source_records'][0],expanded)
        self.assertEqual(item['id'],choice['candidate_id'])
        self.assertEqual(item['raw_value'],self.task['evidence'][0]['raw_value'])
        self.assertEqual(item['source_check'],'通过')
        self.assertEqual(item['applicability_check'],'待核验')
        self.assertEqual(item['calculation_context']['corporate_action_status'],'unknown')
        self.assertNotIn('source_check',expanded)

    def test_cell_choice_rejects_mixed_fields_instead_of_repairing(self):
        _,selection = self.selection()
        for field,value in [('id','other'),('value',999),('identity',None),('source_check','通过')]:
            with self.subTest(field=field),self.assertRaises(ContractError):
                expand_api_cell_selection(self.task,dict(selection,**{field:value}))

    def test_changed_source_or_foreign_cell_reference_is_rejected(self):
        _,selection = self.selection()
        with self.assertRaises(ContractError):
            expand_api_cell_selection(self.task,dict(selection,api_cell_selection='rh_cell_foreign'))
        self.task['source_records'][0]['snapshot']['tables'][0]['table']['close'][0] += 1
        with self.assertRaises(ContractError):
            expand_api_cell_selection(self.task,selection)

    def test_historical_imported_or_scope_changed_sources_offer_no_short_cells(self):
        original = copy.deepcopy(self.task)
        for change in ('historical','imported','scope'):
            with self.subTest(change=change):
                self.task = copy.deepcopy(original)
                if change=='historical':self.task['research_mode']='historical'
                elif change=='imported':self.task['source_records'][0].pop('acquisition')
                else:self.task['question'] += '改变原始研究问题'
                self.assertFalse(api_cell_choices(self.task,self.task['source_records'][0]))

    def test_short_model_output_expands_locations_not_answers_or_approval(self):
        choice,selection = self.selection()
        contract = evidence_contract(self.task)
        source = self.task['source_records'][0]
        view = {'id':source['id'],'kind':'api','snapshot_hash':source['hash']}
        class Selector:
            def reason(self,task,messages):
                return json.dumps({'evidence_specs':[selection]})
        output,receipt = model_object(Selector(),self.task,'extract',
            {'evidence_contract':contract,'untrusted_sources':[view]}, {'evidence_specs'})
        self.assertEqual(output['evidence_specs'][0]['id'],choice['candidate_id'])
        self.assertEqual(receipt['cell_selection_version'],1)
        self.assertNotIn('source_check',output['evidence_specs'][0])
        self.assertNotIn('answers',output)


if __name__ == '__main__':
    unittest.main()
