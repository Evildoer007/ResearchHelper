"""Explicit listed-unit proof, not a fabricated A/C category or live retry."""
import copy
from decimal import Decimal
import json
import tempfile
import unittest

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, METHOD_REGISTRY, calculate, fingerprint, validate_calculation_context
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import candidate_evidence, listed_etf_unit_context_candidates
from researchhelper.financial_sources import original_line_index, selected_original_span
from researchhelper.output_schema import original_span_output_schema, original_span_prompt, decode_original_spans
from researchhelper.planning import research_brief
from researchhelper.reasoning import evidence_contract, extractable_source_views, planning_digest
from researchhelper.reports import compose


RETAINED = ROOT / 'result/acceptance/one-sentence-research-8bhoacvp/task.json'
METHOD = 'etf_daily_nav_premium_v1'


class ListedETFUnitContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = RETAINED.read_bytes()
        cls.saved = json.loads(cls.original)

    def setUp(self):
        self.task = copy.deepcopy(self.saved)
        self.task['listed_etf_unit_context_version'] = 1
        self.views = extractable_source_views(self.task)
        self.schema = original_span_output_schema(self.task, self.views)
        self.payload = dict(untrusted_sources=self.views, evidence_contract=evidence_contract(self.task, self.views))
        self.prompt = original_span_prompt(self.payload, self.task)
        self.source = next(source for source in self.prompt['evidence_contract']['sources'] if source['kind']=='filing')
        self.original_view = next(view for view in self.views if view['kind']=='filing')

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(), self.original)

    def selection(self):
        original = next(view for view in self.prompt['untrusted_sources'] if view['kind']=='filing')
        api = next(view for view in self.prompt['untrusted_sources'] if view['kind']=='api')
        api_source = next(source for source in self.prompt['evidence_contract']['sources'] if source['kind']=='api')
        lines = original_line_index(self.original_view)
        nav = next(line for line in lines if line['part']=='第9页' and '基金份额净值为2.3272元' in line['text'])
        currency = next(line for line in lines if line['part']=='第3页' and '单位：人民币元' in line['text'])
        date = next(anchor for anchor in original['observation_anchors']
                    if anchor['date']=='2026-06-30' and anchor['part']=='第9页')
        identity = self.source['listed_etf_unit_candidates'][0]
        span = dict(start=nav['index'],end=nav['index'])
        proof = identity['span']
        contexts = [dict(field='fund_id',value='588000',span=proof),
                    dict(field='share_class',value=identity['value'],span=proof),
                    dict(field='currency',value='CNY',span=dict(start=currency['index'],end=currency['index'])),
                    dict(field='valuation_date',value='2026-06-30',span=span),
                    dict(field='nav_kind',value='official',span=span)]
        requirements = self.payload['evidence_contract']['question_requirements']['q1']
        api_selection = dict(api_cell_selections=[api_source['cell_choices'][0]['candidate_id']],question_ids=['q1'],
            review=dict(question_ids=['q1'],allowed_natures=['已实现事实'],conditions=['仅当前取得版本'],
                reason='单位测试中的实际单元格引用，不是新增研究。',
                evidence_need_texts=[dict(question_id='q1',texts=[requirements[0]['text']])]))
        numeric = dict(id='projected_listed_unit_nav',source_ref=original['source_ref'],kind='number',
            object='588000.SH',nature='已实现事实',question_ids=['q1'],period='2026-06-30',
            observation_anchor=date['index'],text_span=span,number_occurrence=3,unit='元',unit_span=span,
            contexts=contexts,review=dict(scope='current_original_descriptive_only',
                need_links=[dict(question_id='q1',need_ids=[requirements[1]['id']])]))
        extras = [dict(context,source_ref=original['source_ref']) for context in contexts
                  if context['field'] in ('fund_id','share_class','valuation_date')]
        return dict(version=3,binding_ref=self.prompt['span_binding_ref'],selections=[
            dict(source_ref=api['source_ref'],kind='api',selection=dict(version=2,evidence_specs=[api_selection]),
                 additional_contexts=extras),numeric])

    def checked_inputs(self):
        decoded = decode_original_spans(self.selection(), self.schema, (self.task,self.views))
        results = [candidate_evidence(self.task,next(record for record in self.task['source_records']
            if record['id']==spec['artifact_id']),spec) for spec in decoded['evidence_specs']]
        return decoded, dict(price=results[0],nav=results[1])

    def test_actual_product_fields_offer_optional_identity_not_ac_category_or_approval(self):
        choices = self.source['listed_etf_unit_candidates']
        self.assertTrue(choices)
        self.assertTrue(all(choice['value']=='listed_etf_unit:588000.SH' for choice in choices))
        self.assertTrue(all(not choice['candidate_is_approval'] and not choice['ac_category_inferred'] for choice in choices))
        self.assertIn('交易代码',choices[0]['quote'])
        self.assertIn('基金运作方式',choices[0]['quote'])
        self.assertEqual(selected_original_span(original_line_index(self.original_view), choices[0]['span'])['quote'],choices[0]['quote'])

    def test_actual_mixed_decoder_and_source_gates_reach_comparable_numeric_inputs(self):
        before = fingerprint(self.task)
        _, records = self.checked_inputs()
        parameters = dict(METHOD_REGISTRY[METHOD]['parameters'],valuation_date='2026-06-30')
        validate_calculation_context(METHOD,records,parameters)
        values = {field:record['value'] for field,record in records.items()}
        output = calculate(METHOD,values,parameters)
        expected = (Decimal(str(values['price']))/Decimal(str(values['nav']))-1)*100
        self.assertLessEqual(abs(Decimal(str(output['value']))-expected),Decimal(str(output['tolerance'])))
        self.assertEqual(values,dict(price=2.344,nav=2.3272))
        self.assertEqual(before,fingerprint(self.task))
        self.assertFalse(self.task['calculations'])
        self.assertEqual(self.task['status'],'部分完成')

    def test_foreign_unit_or_unproved_prefixed_identifier_is_rejected(self):
        decoded, _ = self.checked_inputs()
        spec = copy.deepcopy(decoded['evidence_specs'][1])
        source = next(record for record in self.task['source_records'] if record['id']==spec['artifact_id'])
        for mutation in ('foreign','missing_operation'):
            item = copy.deepcopy(spec)
            if mutation=='foreign': item['calculation_context']['share_class']='listed_etf_unit:510300.SH'
            else: item['calculation_context_basis']['share_class']['quote']='交易代码 588000'
            with self.subTest(mutation=mutation), self.assertRaises(ContractError):
                candidate_evidence(self.task,source,item)

    def test_names_code_prefix_and_main_code_without_trading_code_never_identify_units(self):
        cases = ('基金简称 ETF\n基金主代码588000',
                 '基金运作方式 交易型开放式\n基金主代码 588000',
                 '交易代码 588000\n基金运作方式 契约型开放式',
                 '拟交易代码 588000\n基金运作方式 交易型开放式',
                 '计划使用交易代码 588000\n基金运作方式 交易型开放式',
                 '交易代码 588000\n基金运作方式 交易型开放式\n交易代码 588080')
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(listed_etf_unit_context_candidates(dict(parts=[dict(locator='段落1',text=text)]),'588000.SH'),[])

    def test_multiple_classes_elsewhere_in_the_disclosure_block_aggregate_matching(self):
        view = copy.deepcopy(self.original_view)
        view['parts'].append(dict(locator='合成歧义段',text='下属各类基金份额，A类基金份额与C类基金份额净值分别披露。'))
        self.assertEqual(listed_etf_unit_context_candidates(view,'588000.SH'),[])

    def test_another_explicit_trading_code_on_another_page_still_blocks_matching(self):
        view = copy.deepcopy(self.original_view)
        view['parts'].append(dict(locator='合成多对象段',text='交易代码 588080\n基金运作方式 交易型开放式'))
        self.assertEqual(listed_etf_unit_context_candidates(view,'588000.SH'),[])

    def test_legacy_tasks_do_not_gain_new_model_identity_candidates(self):
        self.task.pop('listed_etf_unit_context_version')
        source = next(source for source in evidence_contract(self.task,self.views)['sources'] if source['kind']=='filing')
        self.assertNotIn('listed_etf_unit_candidates',source)


class CurrentScopePlanningTests(unittest.TestCase):
    def test_new_flags_are_frozen_and_current_policy_does_not_revise_a_plan(self):
        with tempfile.TemporaryDirectory(prefix='current-scope-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder),ROOT,offline=True,require_argument_support=True)
            try:
                task = agent.create('解释ETF净值',entry='ETF',research_mode='current',questions=[dict(id='q1',kind='fact',
                    question='解释ETF净值',answer_scope='general_definition',knowledge_needs=['rh_k_etf_premium_measures'])])
                before = fingerprint(task)
                policy = research_brief(task)['current_version_evidence_policy']
                self.assertTrue(policy['advisory_only']); self.assertFalse(policy['automatic_plan_change'])
                self.assertEqual(before,fingerprint(task))
                done = agent.start(task['id']); self.assertEqual(done['status'],'已完成')
                for name in ('listed_etf_unit_context_version','current_scope_planning_version','catalogue_search_navigation_version'):
                    self.assertEqual(compose(done)[name],1)
                historical = copy.deepcopy(task);historical['research_mode']='historical'
                from researchhelper.planning import input_fingerprint
                historical['input_hash']=input_fingerprint(historical)
                self.assertNotIn('current_version_evidence_policy',research_brief(historical))
                legacy = copy.deepcopy(task);legacy.pop('current_scope_planning_version')
                with self.assertRaises(ContractError): research_brief(legacy)
                legacy['input_hash']=input_fingerprint(legacy)
                self.assertNotEqual(planning_digest(task),planning_digest(legacy))
                self.assertNotIn('current_version_evidence_policy',research_brief(legacy))
            finally:
                for worker in agent.close_runtime():
                    worker.join(timeout=5);self.assertFalse(worker.is_alive())


if __name__ == '__main__':
    unittest.main()
