"""Bounded official-data protocol regressions from retained real downloads.

Read-only capture before dispatch; no original trimming, capacity increase,
model response repair, task reopening, or provider calls.
"""
import copy
import base64
import hashlib
import json
from types import SimpleNamespace
import unittest

from tests import ROOT
from researchhelper.contracts import canonical, fingerprint, ContractError
from researchhelper.evidence import data_request_contract
from researchhelper.planning import question_specs
from researchhelper.output_schema import (original_span_output_schema, original_span_prompt,
    provider_request_schema, _original_quote_selector, acquisition_output_schema)
from researchhelper.reasoning import (extractable_source_views, evidence_contract, model_object,
    MAX_CONTEXT_CHARACTERS)
from researchhelper.contracts import validate_evidence
from tests.test_asset_data_acceptance_review import verify_source, load_retained_task


class CurrentOfficialDataProtocolTests(unittest.TestCase):
    @staticmethod
    def restore_marked_text(text):
        import re
        result=[];position=0
        while position<len(text):
            if text.startswith('⟦⟦',position):
                result.append('⟦');position+=2
            elif text.startswith('⟦',position):
                marker=re.match(r'⟦(?:n\d+_\d+|v\d+)⟧',text[position:])
                if marker is None:raise AssertionError('Unknown protocol marker')
                position+=marker.end()
            else:
                result.append(text[position]);position+=1
        return ''.join(result)

    def setUp(self):
        self.task=load_retained_task(ROOT/'result/acceptance/autonomous-data-phase-ebu_db39')
        self.before=fingerprint(self.task)
        self.views=extractable_source_views(self.task)
        self.payload=dict(question=self.task['question'],intent=self.task['intent'],objects=self.task['objects'],
            cutoff=self.task['cutoff'],questions=question_specs(self.task),calculations=self.task['calculation_specs'],
            untrusted_sources=self.views,evidence_contract=evidence_contract(self.task,self.views),field_registry={},source_reading=None)
        self.payload['calculation_input_targets']=self.payload['evidence_contract']['calculation_input_requirements']['proposal_requirements']

    def tearDown(self):self.assertEqual(fingerprint(self.task),self.before)

    def test_two_full_fomc_originals_fit_without_losing_any_text(self):
        schema=original_span_output_schema(self.task,self.views)
        self.assertIsNotNone(schema)
        shown=original_span_prompt(self.payload,self.task)
        wire=provider_request_schema(self.task,schema,'strict_original_spans_v1')
        self.assertLessEqual(len(canonical(shown))+len(canonical(wire)),MAX_CONTEXT_CHARACTERS)
        for original,projected in zip(self.views,shown['untrusted_sources']):
            decoded=[dict(zip(projected['part_columns'],row)) for row in projected['parts']]
            self.assertEqual(len(decoded),len(original['parts']))
            for old,new in zip(original['parts'],decoded):
                text=''.join(line[1] for line in new['indexed_lines'])
                if projected.get('literal_encoding'):text=self.restore_marked_text(text)
                self.assertEqual(old['text'],text)

    def test_statement_headers_not_last_update_or_inferred_dates_supply_addresses(self):
        dates=[]
        for view in self.views:
            selector=_original_quote_selector(self.task,view)
            self.assertIsNotNone(selector)
            dates.extend(selector['dates'])
        self.assertEqual({row['date'] for row in dates},{'2026-09-16','2026-07-29'})
        self.assertTrue(all('Last Update' not in row['quote'] for row in dates))
        changed=copy.deepcopy(self.views[0])
        for part in changed['parts']:
            if part['text']=='September 16, 2026':part['text']='September 17, 2026'
        with self.assertRaises(ContractError):_original_quote_selector(self.task,changed)

    def test_actual_dispatch_route_reaches_compact_original_protocol(self):
        calls=[]
        def capture(task,messages,views):
            calls.append(messages)
            self.assertIn('source_1',messages[-1]['content'])
            raise RuntimeError('offline_capture_only')
        model=SimpleNamespace(supports_structured_analysis=lambda:True,
            supports_financial_references=lambda:True,reason_structured_original_spans=capture,
            reason_structured_extraction=capture)
        with self.assertRaisesRegex(RuntimeError,'offline_capture_only'):
            model_object(model,self.task,'extract',self.payload,{'evidence_specs'})
        self.assertEqual(len(calls),1)

    def test_navigation_body_names_exact_top_level_container_and_binding(self):
        task=load_retained_task(ROOT/'result/acceptance/autonomous-data-phase-hh8_9dxm')
        before=fingerprint(task)
        payload=dict(data_request_contract=data_request_contract(task))
        schema=acquisition_output_schema(task,payload)
        def capture(task,messages,description):
            body=json.loads(messages[-1]['content'])
            self.assertEqual(body['navigation_result_fields'],['version','binding_ref','data_requests','stop_reason'])
            self.assertEqual(body['navigation_result_binding']['binding_ref'],schema['properties']['binding_ref']['enum'][0])
            self.assertEqual(body['data_request_contract'],description['data_request_contract'])
            raise RuntimeError('offline_navigation_capture_only')
        model=SimpleNamespace(supports_structured_analysis=lambda:True,reason_structured_acquisition=capture)
        with self.assertRaisesRegex(RuntimeError,'offline_navigation_capture_only'):
            model_object(model,task,'acquire',payload,{'data_requests','stop_reason'})
        self.assertEqual(fingerprint(task),before)


class CurrentOfficialLiveDataReviewTests(unittest.TestCase):
    def load(self,suffix):
        directory=ROOT/'result/acceptance'/('autonomous-data-phase-'+suffix)
        task=load_retained_task(directory)
        receipt=json.loads((directory/'receipt.json').read_text())
        self.assertEqual(receipt['status'],'data_phase_sample_passed')
        self.assertTrue(receipt['production_unchanged'] and receipt['owned_runtime_closed'] and receipt['source_unchanged'])
        self.assertFalse(receipt['supplied_evidence'] or receipt['supplied_plan'] or receipt['report_accepted'])
        self.assertEqual(task['status'],'已取消')
        return task

    def test_actual_official_html_bytes_and_every_adopted_excerpt_are_bound(self):
        for suffix in ('svv5eqtm','wbl3hv7c'):
            task=self.load(suffix);before=fingerprint(task)
            for source in task['source_records']:
                verify_source(source,task)
                snapshot=source['snapshot']
                raw=base64.b64decode(snapshot['original_base64'],validate=True)
                self.assertEqual(hashlib.sha256(raw).hexdigest(),snapshot.get('original_sha256',snapshot.get('hash')))
            for item in task['evidence']:
                validate_evidence(item,task)
                self.assertIsNone(item['published_at'])
            self.assertEqual(fingerprint(task),before)

    def test_national_cpi_values_match_the_correct_metric_not_another_number(self):
        import re
        task=self.load('svv5eqtm')
        values=[e for e in task['evidence'] if isinstance(e.get('value'),(int,float))]
        self.assertEqual(len(values),2)
        for metric in ('同比','环比'):
            item=next(e for e in values if metric in e['object'])
            match=re.search('全国居民消费价格'+metric+r'上涨(\d+(?:\.\d+)?)%',item['text'])
            self.assertIsNotNone(match)
            self.assertEqual(item['value'],float(match[1]))
            self.assertEqual(item['period'],'2026-09')
            self.assertEqual(item['observation_period'],dict(frequency='month',value='2026-09'))
            self.assertEqual(item['unit'],'%')
        self.assertEqual([s['parameters']['page'] for s in task['source_records']
                          if s['operation']=='release_catalog'],['0','1'])

    def test_two_fomc_dates_and_rate_paragraphs_remain_separate_current_originals(self):
        task=self.load('wbl3hv7c')
        rates=[e for e in task['evidence'] if 'target range for the federal funds rate' in e.get('text','')]
        self.assertEqual(len(rates),2)
        self.assertEqual({e['period'] for e in rates},{'2026-09-16','2026-07-29'})
        self.assertEqual(len({e['artifact_id'] for e in rates}),2)
        for item in rates:
            self.assertFalse(item['availability']['historical_availability_approved'])
            self.assertFalse(item['availability']['event_causality_approved'])
            self.assertEqual(item['value'] if 'value' in item else None,None)
            wrong=copy.deepcopy(item);wrong['published_at']='2026-09-16T00:00:00+00:00'
            with self.assertRaises(ContractError):validate_evidence(wrong,task)
