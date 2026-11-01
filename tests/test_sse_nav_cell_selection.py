"""Official NAV uses the existing explicit-cell decoder, never approval."""
import copy
import json
import unittest

try:
    from . import test_sse_etf_pcf_source as fixtures
    from . import test_sse_etf_price_context as price_fixtures
except ImportError:
    import test_sse_etf_pcf_source as fixtures
    import test_sse_etf_price_context as price_fixtures
from researchhelper.contracts import ContractError,fingerprint
from researchhelper.evidence import (api_cell_choices,api_cell_choice_coverage,
    expand_api_cell_selection,expand_api_cell_selections,candidate_evidence)
from researchhelper.output_schema import api_extraction_output_schema,original_span_output_schema
from researchhelper.reasoning import extractable_source_views,_api_selector_instructions
from researchhelper.planning import input_fingerprint
from researchhelper.reports import compose


class SSENavCellSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.SSEETFPcfSourceTests.setUpClass()

    def setUp(self):
        self.fixture=fixtures.SSEETFPcfSourceTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task=self.fixture.task;self.record=self.fixture.collect()

    def choices(self):
        return api_cell_choices(self.task,self.record)

    def test_new_task_policy_and_complete_literal_nav_catalogue(self):
        self.assertEqual(self.task['official_etf_cell_selection_version'],1)
        choices=self.choices();self.assertEqual(len(choices),1)
        item=choices[0]
        self.assertEqual((item['value'],item['unit'],item['period']),(4.1856,'元','2026-10-21'))
        self.assertEqual(item['record_locator'],dict(row=0,field='unit_nav'))
        self.assertEqual(item['calculation_context']['valuation_date'],'2026-10-21')
        self.assertNotIn('price_kind',item['calculation_context'])
        self.assertNotIn('corporate_action_status',item['calculation_context'])
        self.assertFalse(item['source_approved'])
        self.assertTrue(api_cell_choice_coverage(self.record,choices)['selection_complete'])

    def test_legacy_historical_and_document_tasks_gain_no_new_choices(self):
        for changes in (dict(official_etf_cell_selection_version=None),
                dict(official_etf_cell_selection_version=True),dict(official_etf_cell_selection_version=2),
                dict(research_mode='historical'),dict(intent='原件内容核对')):
            task=dict(self.task,**changes)
            self.assertEqual(api_cell_choices(task,self.record),[])

    def test_explicit_choice_expands_original_value_without_financial_approval(self):
        choice=self.choices()[0];before=copy.deepcopy((self.task,self.record))
        spec=dict(api_cell_selection=choice['candidate_id'],question_ids=['q1'],review={})
        expanded=expand_api_cell_selection(self.task,spec)
        self.assertEqual(expanded['record_locator'],choice['record_locator'])
        item=candidate_evidence(self.task,self.record,expanded)
        self.assertEqual((item['value'],item['unit'],item['period']),(4.1856,'元','2026-10-21'))
        self.assertEqual(item['source_check'],'通过');self.assertEqual(item['applicability_check'],'待核验')
        self.assertEqual((self.task,self.record),before)
        self.assertEqual(self.task['evidence'],[]);self.assertEqual(self.task['calculations'],[])

    def test_unknown_duplicate_or_foreign_cell_cannot_be_adopted(self):
        ident=self.choices()[0]['candidate_id']
        for ids in (['foreign'],[ident,ident]):
            with self.assertRaises(ContractError):
                expand_api_cell_selections(self.task,[dict(api_cell_selections=ids,question_ids=['q1'],review={})])
        other=copy.deepcopy(self.task);other['source_records']=[]
        with self.assertRaises(ContractError):
            expand_api_cell_selection(other,dict(api_cell_selection=ident,question_ids=['q1'],review={}))

    def test_changed_original_bytes_or_valuation_date_invalidates_catalogue(self):
        for field in ('valuation_date','unit_nav'):
            record=copy.deepcopy(self.record)
            if field=='valuation_date':record['snapshot']['exchange_origin'][field]='2026-10-20'
            else:record['snapshot']['data'][0][field]=99
            record['hash']=fingerprint(record['snapshot'])
            self.assertEqual(api_cell_choices(self.task,record),[])

    def test_incomplete_share_class_is_not_inferred(self):
        from unittest.mock import patch
        from researchhelper.evidence import _api_calculation_context_values
        values=_api_calculation_context_values(self.record,dict(row=0,field='unit_nav'),dict(object='510300.SH'))
        values.pop('share_class')
        with patch('researchhelper.evidence._api_calculation_context_values',return_value=values):
            self.assertEqual(self.choices(),[])

    def test_api_schema_selects_cells_and_old_policy_retains_full_object(self):
        views=extractable_source_views(self.task)
        schema=api_extraction_output_schema(self.task,views)
        self.assertEqual(schema['properties']['version']['enum'],[2])
        self.assertIn('api_cell_selections',schema['properties']['evidence_specs']['items']['anyOf'][0]['properties'])
        old=copy.deepcopy(self.task);old.pop('official_etf_cell_selection_version')
        old_schema=api_extraction_output_schema(old,extractable_source_views(old))
        self.assertEqual(old_schema['properties']['version']['enum'],[1])

    def test_instructions_describe_actual_per_source_grammar_not_assumed_cells(self):
        old=copy.deepcopy(self.task);old.pop('official_etf_cell_selection_version')
        def wrapped(task):
            api=api_extraction_output_schema(task,extractable_source_views(task))
            return dict(properties=dict(selections=dict(items=dict(anyOf=[dict(properties=dict(
                kind=dict(enum=['api']),source_ref=dict(enum=['source_1']),selection=api))]))))
        text=_api_selector_instructions(wrapped(old))
        self.assertIn('selection.version=1',text);self.assertIn('api_value_reference',text)
        current=_api_selector_instructions(wrapped(self.task))
        self.assertIn('selection.version=2',current);self.assertIn('api_cell_selections',current)

    def test_policy_binds_inputs_and_report_freeze_without_budget_change(self):
        before=copy.deepcopy(self.task['ledger'])
        old=copy.deepcopy(self.task);old.pop('official_etf_cell_selection_version')
        self.assertNotEqual(input_fingerprint(self.task),input_fingerprint(old))
        self.fixture.agent._adopt_method_knowledge(self.task)
        self.task['status']='部分完成'
        self.task['gaps']=['尚未提交适用性核验，不形成研究结论。']
        report=compose(self.task)
        self.assertIn('official_etf_cell_selection_version',json.dumps(report))
        self.assertEqual(self.task['ledger'],before)

    def test_actual_mixed_quote_and_nav_use_the_same_cell_grammar(self):
        control=price_fixtures.SSEETFPcfPriceContextTests();control.setUp();self.addCleanup(control.doCleanups)
        task=control.task
        schema=original_span_output_schema(task,extractable_source_views(task))
        choices=schema['properties']['selections']['items']['anyOf']
        self.assertTrue(choices)
        self.assertTrue(all(choice['properties']['selection']['properties']['version']['enum'] in ([2],[3])
            for choice in choices))
        text=_api_selector_instructions(schema)
        self.assertIn('api_cell_selections',text);self.assertNotIn('api_value_reference',text)
        old=copy.deepcopy(task);old.pop('official_etf_cell_selection_version')
        legacy=original_span_output_schema(old,extractable_source_views(old))
        legacy_text=_api_selector_instructions(legacy)
        self.assertIn('api_value_reference',legacy_text)
        self.assertIn('api_cell_selections',legacy_text)


if __name__=='__main__':
    unittest.main()
