"""Literal reading navigation and exact request-field instructions."""
import copy
from types import SimpleNamespace
import unittest

from tests import ROOT
from tests.test_asset_data_acceptance_review import load_retained_task
from tests.test_operating_original_protocol import payload
from researchhelper.contracts import fingerprint,canonical
from researchhelper.evidence import data_request_contract
from researchhelper.output_schema import (_original_span_choices,original_span_prompt,
    acquisition_output_schema,_present_read_query_navigation)
from researchhelper.reasoning import model_object


class ReadQueryNavigationTests(unittest.TestCase):
    def setUp(self):
        self.task=load_retained_task(ROOT/'result/acceptance/autonomous-data-phase-7gu8uc_u')
        self.views=self.task['reasoning_state']['reading']['bundle']['views']
        self.before=fingerprint(self.task)
        self.choices=_original_span_choices(self.task,self.views)

    def tearDown(self):
        self.assertEqual(fingerprint(self.task),self.before)

    def test_all_index_hits_are_literal_lines_of_actual_reading_queries(self):
        prompt=original_span_prompt(payload(self.task,self.views),self.task)
        index=prompt['untrusted_sources'][0]['read_query_navigation']
        self.assertEqual(index['queries'],self.task['reasoning_state']['reading']['bundle']['selection'][0]['queries'])
        self.assertFalse(index['index_omitted'] or index['financial_approval'])
        self.assertTrue(index['source_text_preserved'])
        for query,line in index['hits']:
            self.assertIn(index['queries'][query].casefold(),self.choices[0]['lines'][line]['text'].casefold())
        # The model's reading query guessed 千元, but the actual header is 元.
        # A missing query hit must not manufacture that unit or change text.
        wrong=index['queries'].index('单位：人民币千元')
        self.assertFalse(any(query==wrong for query,line in index['hits']))

    def test_other_views_or_source_selection_do_not_inherit_the_index(self):
        for change in ('view','selection'):
            task=copy.deepcopy(self.task)
            if change=='view':task['reasoning_state']['reading']['bundle']['views']=[]
            else:task['reasoning_state']['reading']['bundle']['selection'][0]['artifact_id']='other_source'
            shown=dict(untrusted_sources=[dict(source_ref=self.choices[0]['source_ref'])])
            result=_present_read_query_navigation(shown,task,self.choices)
            self.assertNotIn('read_query_navigation',result['untrusted_sources'][0])

    def test_large_optional_index_is_explicitly_omitted_without_changing_text(self):
        task=copy.deepcopy(self.task);choices=copy.deepcopy(self.choices)
        task['reasoning_state']['reading']['bundle']['selection'][0]['queries']=['literal']
        choices[0]['lines']=[dict(index=i,text='literal') for i in range(257)]
        shown=dict(untrusted_sources=[dict(parts=copy.deepcopy(self.views[0]['parts']))])
        before=canonical(shown['untrusted_sources'][0]['parts'])
        result=_present_read_query_navigation(shown,task,choices)['untrusted_sources'][0]
        self.assertEqual(result['read_query_navigation']['literal_hits'],257)
        self.assertTrue(result['read_query_navigation']['index_omitted'])
        self.assertEqual(result['read_query_navigation']['hits'],[])
        self.assertEqual(canonical(result['parts']),before)

    def test_navigation_request_fields_are_derived_from_current_schema(self):
        task=load_retained_task(ROOT/'result/acceptance/autonomous-data-phase-6thpue1q')
        body=dict(data_request_contract=data_request_contract(task))
        schema=acquisition_output_schema(task,body);captured=[]
        def capture(task,messages,description):
            captured.append(messages[0]['content'])
            raise RuntimeError('offline_navigation_shape_capture')
        model=SimpleNamespace(supports_structured_analysis=lambda:True,reason_structured_acquisition=capture)
        with self.assertRaisesRegex(RuntimeError,'offline_navigation_shape_capture'):
            model_object(model,task,'acquire',body,{'data_requests','stop_reason'})
        self.assertEqual(len(captured),1)
        self.assertIn('data_requests每项必须且仅含',captured[0])
        for branch in schema['properties']['data_requests']['items'].get('anyOf',[]):
            self.assertIn(canonical(list(branch['properties'])),captured[0])


if __name__=='__main__':
    unittest.main()
