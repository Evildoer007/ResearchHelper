"""Question-required numeric selection, including non-adjacent row units.

Offline protocol checks of retained real originals, not model-quality passes.
No old task is resumed and no original amount or unit is changed.
"""
import copy
from types import SimpleNamespace
import unittest

from tests import ROOT
from tests.test_asset_data_acceptance_review import load_retained_task
from tests.test_operating_original_protocol import payload
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.output_schema import (_original_span_choices, original_span_output_schema,
    decode_original_spans)
from researchhelper.reasoning import model_object, REQUESTED_VALUE_EXTRACTION_RULE


class RequestedValueExtractionTests(unittest.TestCase):
    def setUp(self):
        self.folder = ROOT/'result/acceptance/autonomous-data-phase-r91uw48x'
        self.task = load_retained_task(self.folder)
        self.before = fingerprint(self.task)
        self.views = self.task['reasoning_state']['reading']['bundle']['views']
        self.choices = _original_span_choices(self.task, self.views)
        self.schema = original_span_output_schema(self.task, self.views)

    def tearDown(self):
        self.assertEqual(fingerprint(load_retained_task(self.folder)), self.before)

    def wire(self):
        choice = self.choices[0]
        numeric = next(v['properties'] for v in self.schema['properties']['selections']['items']['anyOf']
                       if v['properties']['kind']['enum'] == ['number'])
        needs = numeric['review']['properties']['need_links']['items']['anyOf'][0]['properties']
        period = next(p for p in choice['period_anchors'] if p['period'] == '2026-01-01/2026-06-30')
        row = next(line for line in choice['lines']
                   if line['part'] == '第17页表1' and '电池系统（GWh）' in line['text'])
        tokens = [token for token in choice['number_tokens'] if token['line'] == row['index']]
        selections = []
        for ident, token_text in (('capacity', '525'), ('production', '498')):
            token = next(token for token in tokens if token['token'] == token_text)
            selections.append(dict(id='ev_'+ident, source_ref=choice['source_ref'], kind='number',
                object=numeric['object']['enum'][0], nature='已实现事实',
                temporal_reference=dict(period_ref=period['ref']),
                number_reference=dict(number_ref=token['ref'],unit='GWh',unit_reference=dict(line=row['index'])),
                review=dict(scope='current_original_descriptive_only',need_links=[dict(
                    question_id=needs['question_id']['enum'][0],need_ids=[needs['need_ids']['items']['enum'][0]])])))
        return dict(version=12,binding_ref=self.schema['properties']['binding_ref']['enum'][0],selections=selections)

    def test_independent_unit_branch_restores_capacity_and_production_without_conversion(self):
        result=decode_original_spans(self.wire(),self.schema,(self.task,self.views))['evidence_specs']
        self.assertEqual([e['value'] for e in result],[525,498])
        self.assertTrue(all(e['raw_unit']==e['unit']=='GWh' and e['conversion_factor']==1 for e in result))
        self.assertTrue(all(e['record_locator']['part']=='第17页表1' for e in result))
        self.assertTrue(all(e['period']=='2026-01-01/2026-06-30' for e in result))
        self.assertTrue(all(e['observation_date']=='2026-06-30' for e in result))
        self.assertFalse(any('source_check' in e or 'applicability_check' in e for e in result))

    def test_unknown_unit_or_other_source_reference_is_not_guessed(self):
        for field,value in (('unit','MWh'),('number_ref','n2_1')):
            wire=self.wire();wire['selections'][0]['number_reference'][field]=value
            with self.subTest(field=field),self.assertRaises(ContractError):
                decode_original_spans(wire,self.schema,(self.task,self.views))

    def test_capacity_and_construction_capacity_remain_distinct_original_tokens(self):
        wire=self.wire()
        row_index=wire['selections'][0]['number_reference']['unit_reference']['line']
        tokens=[t for t in self.choices[0]['number_tokens'] if t['line']==row_index]
        self.assertEqual([t['token'] for t in tokens],['525','764','94.86','498'])
        self.assertNotEqual(tokens[0]['ref'],tokens[1]['ref'])
        self.assertEqual(wire['selections'][0]['number_reference']['number_ref'],tokens[0]['ref'])

    def test_actual_extract_prompt_targets_requested_values_even_without_methods(self):
        for suffix in ('r91uw48x','pfoiaixv'):
            task=load_retained_task(ROOT/'result/acceptance'/('autonomous-data-phase-'+suffix))
            self.assertFalse(any(q.get('method_needs') for q in task['questions']))
            views=task['reasoning_state']['reading']['bundle']['views'];calls=[]
            def capture(task,messages,views):
                calls.append(messages[0]['content'])
                raise RuntimeError('offline_requested_value_capture')
            model=SimpleNamespace(supports_structured_analysis=lambda:True,supports_financial_references=lambda:True,
                reason_structured_original_spans=capture,reason_structured_extraction=capture)
            with self.assertRaisesRegex(RuntimeError,'offline_requested_value_capture'):
                model_object(model,copy.deepcopy(task),'extract',payload(task,views),{'evidence_specs'})
            self.assertEqual(len(calls),1)
            self.assertIn(REQUESTED_VALUE_EXTRACTION_RULE,calls[0])
            self.assertIn('即使没有计算方法',calls[0])
            self.assertIn('不将文字摘录声称为可计算字段',calls[0])


if __name__=='__main__':
    unittest.main()
