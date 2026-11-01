"""Expose existing explicit links, not inferred or automatically adopted roles."""
import copy
import json
import unittest

try:
    from . import test_sse_etf_price_context as fixtures
except ImportError:
    import test_sse_etf_price_context as fixtures

from researchhelper.contracts import ContractError,calculation_context_contract,validate_calculation_context
from researchhelper.evidence import candidate_evidence
from researchhelper.output_schema import (original_span_output_schema,original_span_prompt,
    _api_context_selection_guide,decode_original_spans)
from researchhelper.reasoning import extractable_source_views,evidence_contract,model_object
from researchhelper.planning import question_specs
from researchhelper.model import ModelService


class APIContextGuideTests(unittest.TestCase):
    def setUp(self):
        self.control=fixtures.SSEETFPcfPriceContextTests();self.control.setUp()
        self.addCleanup(self.control.doCleanups)
        self.task=self.control.task;self.views=extractable_source_views(self.task)

    def payload(self):
        return dict(question=self.task['question'],intent=self.task['intent'],objects=self.task['objects'],
            cutoff=self.task['cutoff'],questions=question_specs(self.task),calculations=self.task['calculation_specs'],
            untrusted_sources=self.views,evidence_contract=evidence_contract(self.task,self.views),
            field_registry={},source_reading=None)

    def guide(self):
        return original_span_prompt(self.payload(),self.task)['api_supplementary_contexts']

    def test_guide_has_exact_source_cell_and_allowed_context_not_approval(self):
        guide=self.guide();self.assertFalse(guide['candidate_is_approval']);self.assertFalse(guide['automatic_selection'])
        self.assertEqual(len(guide['candidates']),1);row=guide['candidates'][0]
        self.assertEqual((row['source_ref'],row['basis_source_ref'],row['period']),('source_2','source_1','2026-10-21'))
        self.assertEqual((row['supplied_field'],row['supplied_value']),('share_class','listed_etf_unit:510300.SH'))
        self.assertEqual(len(row['api_cell_selections']),1)
        self.assertEqual(set(row['additional_contexts'][0]),{'context_ref'})
        self.assertNotIn('snapshot',json.dumps(guide));self.assertNotIn('api_key',json.dumps(guide))

    def test_index_does_not_change_schema_task_or_extracted_values(self):
        schema=original_span_output_schema(self.task,self.views)
        before=copy.deepcopy((self.task,self.views,schema))
        self.guide()
        self.assertEqual((self.task,self.views,original_span_output_schema(self.task,self.views)),before)
        self.assertEqual(self.task['evidence'],[]);self.assertEqual(self.task['calculations'],[])

    def test_explicit_guide_selection_has_real_nav_basis_and_comparable_roles(self):
        views,schema,wire=self.control.wire();row=self.guide()['candidates'][0]
        selected=wire['selections'][0]
        self.assertEqual(selected['source_ref'],row['source_ref'])
        self.assertEqual(selected['selection']['evidence_specs'][0]['api_cell_selections'],row['api_cell_selections'])
        selected['additional_contexts']=copy.deepcopy(row['additional_contexts'])
        spec=decode_original_spans(wire,schema,(self.task,views))['evidence_specs'][0]
        fact=candidate_evidence(self.task,self.control.price,spec)
        self.assertEqual(fact['calculation_context_basis']['share_class']['artifact_id'],self.control.nav['id'])
        self.assertEqual(fact['applicability_check'],'待核验')
        validate_calculation_context(self.control.method,dict(price=fact,nav=self.control.bindings['nav']),
            self.control.parameters,context_contract=calculation_context_contract(self.task,self.control.method))

    def test_omission_does_not_fill_or_approve_the_missing_role(self):
        views,schema,wire=self.control.wire();wire['selections'][0]['additional_contexts']=[]
        spec=decode_original_spans(wire,schema,(self.task,views))['evidence_specs'][0]
        fact=candidate_evidence(self.task,self.control.price,spec)
        self.assertNotIn('share_class',fact['calculation_context'])
        with self.assertRaises(ContractError):
            validate_calculation_context(self.control.method,dict(price=fact,nav=self.control.bindings['nav']),
                self.control.parameters,context_contract=calculation_context_contract(self.task,self.control.method))

    def test_unread_nav_cannot_enter_the_index(self):
        views=[v for v in self.views if v['id']!=self.control.nav['id']]
        self.assertIsNone(original_span_output_schema(self.task,views))
        self.assertEqual(_api_context_selection_guide(self.task,[],None),[])

    def test_legacy_share_policy_does_not_gain_new_links(self):
        task=copy.deepcopy(self.task);task['official_etf_pcf_version']=1
        self.assertIsNone(original_span_output_schema(task,self.views))
        self.assertEqual(_api_context_selection_guide(task,[],None),[])

    def test_foreign_reference_is_still_rejected_by_the_existing_decoder(self):
        views,schema,wire=self.control.wire()
        row=self.guide()['candidates'][0];selected=copy.deepcopy(row['additional_contexts'])
        selected[0]['context_ref']='foreign';wire['selections'][0]['additional_contexts']=selected
        with self.assertRaises(ContractError):decode_original_spans(wire,schema,(self.task,views))

    def test_model_sees_selection_location_and_no_automatic_context_rule(self):
        class Captured(Exception):pass
        class Observer:
            def supports_structured_analysis(self):return True
            def input_budget(self):
                return ModelService.documented_input_budget(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash'))
            def reason_structured_original_spans(self,task,messages,views):
                self.messages=messages;raise Captured()
        observer=Observer()
        with self.assertRaises(Captured):model_object(observer,self.task,'extract',self.payload(),{'evidence_specs'})
        self.assertIn('API选择项外层',observer.messages[0]['content'])
        self.assertIn(self.guide()['candidates'][0]['additional_contexts'][0]['context_ref'],observer.messages[-1]['content'])


if __name__=='__main__':unittest.main()
