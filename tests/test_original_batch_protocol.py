"""Replay the failed two-original reading, without a model or supplier call."""
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint, evidence_requirements
from researchhelper.evidence import candidate_evidence
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.financial_sources import expand_financial_value_references
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (original_batch_output_schema, original_batch_prompt,
    decode_original_batch, financial_reference_output_schema, ORIGINAL_BATCH_PROTOCOL,
    ORIGINAL_BATCH_FUNCTION_NAME)
from researchhelper.reasoning import evidence_contract, model_object, validate_model_specs
from researchhelper.source_reader import validate_read_specs

ROOT = Path(__file__).resolve().parents[1]
RETAINED = ROOT/'result/acceptance/one-sentence-research-xjnv_5gx/task.json'


class OriginalBatchProtocolTests(unittest.TestCase):
    def setUp(self):
        self.original = RETAINED.read_bytes()
        self.task = json.loads(self.original)
        self.bundle = self.task['reasoning_state']['reading']['bundle']
        self.views = self.bundle['views']
        self.payload = dict(untrusted_sources=self.views, evidence_contract=evidence_contract(self.task, self.views))
        self.schema = original_batch_output_schema(self.task, self.views)
        self.presented = original_batch_prompt(self.payload, self.task)
        choices = self.presented['source_choices']
        self.financial_index = next(i for i,c in enumerate(choices) if c['selection_kind']=='financial')
        self.quote_index = next(i for i,c in enumerate(choices) if c['selection_kind']=='qualitative')
        fin_view = self.presented['untrusted_sources'][self.financial_index]
        source = self.presented['evidence_contract']['sources'][self.financial_index]
        question = next(q for q in self.task['questions'] if q.get('method_needs'))
        links = [dict(question_id=question['id'], need_ids=[n['id'] for n in evidence_requirements(question)
            if '合并利润表' in n['text'] or '合并现金流量表' in n['text']])]
        anchor = next(a['index'] for a in fin_view['observation_anchors'] if a['date']=='2026-06-30')
        cells = []
        for metric in ('revenue','net_profit','operating_cash_flow'):
            cell = next(c for c in source['financial_context_cells'] if c['context']['metric']==metric
                        and c['context']['period']=='2026年半年度')
            cells.append(dict(id='probe_'+metric, object=source['object_binding']['value'],
                nature='已实现事实', question_ids=[question['id']], observation_anchor=anchor,
                financial_context_reference=cell['candidate_id'],
                shared_context_references={field:next(c['candidate_id'] for c in
                    source['financial_shared_context_choices'][field] if c['value']==value)
                    for field,value in (('entity_id',source['financial_shared_context_choices']['entity_id'][0]['value']),
                                        ('accounting_standard','CAS'))},
                review=dict(scope='current_original_descriptive_only', need_links=links)))
        financial = dict(version=8, source_reference=choices[self.financial_index]['binding'],
            current_version_only=True, financial_cells=cells, text_quotes=[])
        quote_view = self.presented['untrusted_sources'][self.quote_index]
        quote_source = self.presented['evidence_contract']['sources'][self.quote_index]
        target = next(q for q in self.task['questions'] if q['id']=='q3')
        quote = next((ref,text) for part in quote_view['parts'] for ref,text in part['quote_choices']
                     if '会计政策变更' in text)
        qualitative = dict(binding_ref=choices[self.quote_index]['binding']['binding_ref'], text_quotes=[dict(
            id='probe_accounting_policy', object=quote_source['object_binding']['value'], nature='已实现事实',
            question_ids=[target['id']], observation_anchor=next(a['index'] for a in
                quote_view['observation_anchors'] if a['date']=='2026-01-01'),
            quote_reference=quote[0], review=dict(scope='current_original_descriptive_only',
                need_links=[dict(question_id=target['id'],need_ids=[n['id'] for n in evidence_requirements(target)])]))])
        self.wire = dict(version=1, binding_ref=self.presented['batch_binding_ref'], sources=[
            dict(source_ref=choices[self.financial_index]['source_ref'], selection=financial),
            dict(source_ref=choices[self.quote_index]['source_ref'], selection=qualitative)])
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No network'))
        guard.start(); self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(), self.original)

    def decode(self, wire=None):
        return decode_original_batch(self.wire if wire is None else wire, self.schema, (self.task,self.views))

    def service(self):
        temporary = tempfile.TemporaryDirectory(prefix='batch-service-', dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        settings = ModelSettings(temporary.name)
        settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
            api_key='FAKE_BATCH_KEY', extraction_mode='direct', reasoning_effort='low'))
        self.task['ledger']['used']['model_calls'] = 0
        return ModelService(settings, offline=False)

    def response(self):
        return dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message',role='assistant',status='completed',
                content=[dict(type='output_text',text=json.dumps(self.wire))])],usage={})

    def expanded(self):
        return expand_financial_value_references(self.task, self.decode()['evidence_specs'], self.views)

    def test_actual_two_sources_are_available_without_dropping_policy_announcement(self):
        self.assertIsNotNone(self.schema)
        self.assertEqual(len(self.presented['source_choices']), 2)
        specs = self.decode()['evidence_specs']
        self.assertEqual(len(specs), 4)
        self.assertEqual({s['artifact_id'] for s in specs}, {v['id'] for v in self.views})

    def test_every_source_keeps_all_original_read_characters_separately(self):
        for before,after in zip(self.views, self.presented['untrusted_sources']):
            self.assertEqual(before['id'], after['id'])
            for first,second in zip(before['parts'],after['parts']):
                self.assertEqual(''.join(text for _,text in second['quote_choices']), first['text'])

    def test_expanded_proposals_pass_real_read_source_and_applicability_gates(self):
        specs = self.expanded()
        validate_model_specs(self.task, specs)
        validate_read_specs(self.task, specs, self.bundle)
        for spec in specs:
            original = next(r for r in self.task['source_records'] if r['id']==spec['artifact_id'])
            item = candidate_evidence(self.task, original, spec)
            reviewed = ResearchAnalyzer().assess(self.task,item,spec['review'],self.task['source_records'])
            self.assertEqual(reviewed['source_check'], '通过')
            self.assertEqual(reviewed['applicability_check'], '通过')
        self.assertFalse(self.task['evidence'])  # No adoption or financial conclusion.

    def test_policy_quotation_and_date_remain_bound_to_policy_not_financial_report(self):
        spec = self.decode()['evidence_specs'][-1]
        self.assertEqual(spec['artifact_id'],self.views[self.quote_index]['id'])
        self.assertEqual(spec['observation_date'],'2026-01-01')
        self.assertEqual(spec['unit'],'不适用')
        self.assertNotIn('value',spec)

    def test_swapping_source_labels_cannot_rebind_selected_cells_or_dates(self):
        wire=copy.deepcopy(self.wire)
        wire['sources'][0]['source_ref'],wire['sources'][1]['source_ref'] = (
            wire['sources'][1]['source_ref'],wire['sources'][0]['source_ref'])
        with self.assertRaises(ContractError):self.decode(wire)

    def test_duplicate_source_is_not_merged_or_counted_as_independent(self):
        wire=copy.deepcopy(self.wire);wire['sources'].append(copy.deepcopy(wire['sources'][0]))
        with self.assertRaises(ContractError):self.decode(wire)

    def test_changed_task_or_view_invalidates_entire_batch(self):
        task=copy.deepcopy(self.task);task['version']+=1
        with self.assertRaises(ContractError):decode_original_batch(self.wire,self.schema,(task,self.views))
        views=copy.deepcopy(self.views);views[1]['parts'][0]['text']+='changed'
        with self.assertRaises(ContractError):decode_original_batch(self.wire,self.schema,(self.task,views))

    def test_unknown_quote_is_not_repaired_to_a_nearby_reference(self):
        wire=copy.deepcopy(self.wire);wire['sources'][1]['selection']['text_quotes'][0]['quote_reference']='quote_99999'
        with self.assertRaises(ContractError):self.decode(wire)

    def test_qualitative_original_cannot_submit_financial_values(self):
        wire=copy.deepcopy(self.wire);wire['sources'][1]['selection']['financial_cells']=[]
        with self.assertRaises(ContractError):self.decode(wire)

    def test_actual_unknown_first_publication_remains_unknown(self):
        specs=self.decode()['evidence_specs']
        self.assertTrue(all(spec['published_at'] is None for spec in specs))
        self.assertTrue(all(spec['review']['temporal_use']=='current_version' for spec in specs))

    def test_protocol_does_not_mutate_task_source_or_wire(self):
        before=copy.deepcopy((self.task,self.views,self.wire))
        self.decode()
        self.assertEqual((self.task,self.views,self.wire),before)

    def test_no_api_or_method_is_removed_to_fit_the_container(self):
        self.assertIsNone(original_batch_output_schema(self.task,self.views+[{'kind':'api','id':'api'}]))
        task=copy.deepcopy(self.task);task['questions'][0]['method_needs'].append('fee_difference_v1')
        self.assertIsNone(original_batch_output_schema(task,self.views))

    def test_single_source_keeps_its_existing_short_reference_protocol(self):
        self.assertIsNone(original_batch_output_schema(self.task,[self.views[self.financial_index]]))
        self.assertIsNotNone(financial_reference_output_schema(self.task,[self.views[self.financial_index]]))

    def test_official_transport_selects_both_sources_in_one_budgeted_call(self):
        service=self.service()
        with patch.object(service,'_request_bytes',return_value=json.dumps(self.response()).encode()) as request:
            value=service.reason_structured_original_batch(self.task,[],self.views)
        self.assertEqual(json.loads(value),self.decode())
        self.assertEqual(self.task['ledger']['used']['model_calls'],1)
        self.assertEqual(json.loads(request.call_args.args[1].data)['text']['format']['name'],ORIGINAL_BATCH_FUNCTION_NAME)

    def test_normal_model_phase_uses_batch_protocol_and_ordinary_expansion(self):
        service=self.service()
        with patch.object(service,'_request_bytes',return_value=json.dumps(self.response()).encode()):
            value,_=model_object(service,self.task,'extract',self.payload,{'evidence_specs'})
        self.assertEqual(value,dict(evidence_specs=self.expanded()))
        record=self.task['ledger']['dispatches'][-1]
        self.assertEqual(record['output_protocol'],ORIGINAL_BATCH_PROTOCOL)
        self.assertEqual(record['output_schema_hash'],fingerprint(self.schema))


if __name__=='__main__':unittest.main()
