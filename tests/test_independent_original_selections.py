"""Offline independent-selection regressions on unchanged retained originals.

    Invalid choices are synthetic. A decoded specification is not evidence,
    question completion, a financial approval or a live-model acceptance.
"""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock

from tests import ROOT
from tests import test_typed_original_requirements as fixtures
from tests.test_operating_original_protocol import payload
from researchhelper.contracts import ContractError,fingerprint
from researchhelper.data_requirements import requirement_slots
from researchhelper.output_schema import (decode_original_spans,original_span_output_schema,
    _original_span_choices,ORIGINAL_SELECTION_REJECTIONS,validated_original_selection_rejections)
from researchhelper.planning import input_fingerprint
from researchhelper.model import ModelSettings,ModelService
from researchhelper.reasoning import model_object,PHASE_FIELDS
from researchhelper.engine import ResearchAgent
from researchhelper.data_requirements import pending
from researchhelper.reports import compose

POLICY='independent_original_selections_version'


class IndependentOriginalSelectionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.TypedOriginalRequirementTests.setUpClass()

    def setUp(self):
        self.task=copy.deepcopy(fixtures.TypedOriginalRequirementTests.task);self.task[POLICY]=1
        self.task['input_hash']=input_fingerprint(self.task)
        self.views=copy.deepcopy(fixtures.TypedOriginalRequirementTests.views)
        self.schema=original_span_output_schema(self.task,self.views)
        self.choices={c['source_ref']:c for c in _original_span_choices(self.task,self.views)}
        self.slots=requirement_slots(self.task)
        self.slot=next(s for s in self.slots if s['constraints']['value_kind']=='number')
        guard=patch('socket.socket.connect',side_effect=AssertionError('Offline only'))
        guard.start();self.addCleanup(guard.stop)

    wire=fixtures.TypedOriginalRequirementTests.wire
    item=fixtures.TypedOriginalRequirementTests.item

    def selections(self):
        good=self.item(self.slot);good['id']='valid_choice'
        bad=copy.deepcopy(good);bad['id']='invalid_choice'
        bad['number_reference']['number_ref']='unknown_number_reference'
        wire=self.wire();wire['requirements'][self.slot['key']]=dict(status='found',items=[good,bad])
        return wire

    def decode(self,wire=None):
        return decode_original_spans(wire or self.selections(),self.schema,(self.task,self.views))

    def test_bad_quantity_address_does_not_erase_valid_same_requirement_item(self):
        wire=self.selections();before=fingerprint((self.task,self.views,wire))
        result=self.decode(wire)
        self.assertEqual([(s['id'],s['value'],s['unit']) for s in result['evidence_specs']],
                         [('valid_choice',0.15,'%')])
        diagnostic=result[ORIGINAL_SELECTION_REJECTIONS]
        self.assertEqual((diagnostic['submitted_items'],diagnostic['accepted_items']), (2,1))
        self.assertEqual(len(diagnostic['rejected_items']),1)
        self.assertNotIn('unknown_number_reference',json.dumps(result))
        self.assertEqual(before,fingerprint((self.task,self.views,wire)))

    def test_fully_valid_batch_has_identical_canonical_result_and_no_warning(self):
        wire=self.selections();wire['requirements'][self.slot['key']]['items'].pop()
        actual=self.decode(wire)
        legacy=copy.deepcopy(self.task);legacy.pop(POLICY)
        legacy['input_hash']=input_fingerprint(legacy)
        schema=original_span_output_schema(legacy,self.views)
        wire['binding_ref']=schema['properties']['binding_ref']['enum'][0]
        expected=decode_original_spans(wire,schema,(legacy,self.views))
        self.assertEqual(actual,expected)
        self.assertNotIn(ORIGINAL_SELECTION_REJECTIONS,actual)

    def test_legacy_batch_remains_strict_no_old_failure_is_reopened(self):
        legacy=copy.deepcopy(self.task);legacy.pop(POLICY)
        with self.assertRaises(ContractError):
            decode_original_spans(self.selections(),self.schema,(legacy,self.views))

    def test_all_rejected_preserves_empty_specs_not_approval_or_completion(self):
        wire=self.selections();wire['requirements'][self.slot['key']]['items'].pop(0)
        result=self.decode(wire);diagnostic=result[ORIGINAL_SELECTION_REJECTIONS]
        self.assertEqual(result['evidence_specs'],[])
        self.assertEqual(diagnostic['accepted_items'],0)
        self.assertFalse(diagnostic['automatic_repair'] or diagnostic['evidence_approved'] or diagnostic['coverage_approved'])
        self.assertEqual(self.task['evidence'],fixtures.TypedOriginalRequirementTests.task['evidence'])

    def test_wrong_binding_container_extra_authority_and_source_still_fail_whole_envelope(self):
        for change in ('binding','unknown_field','authority','source','unscoped'):
            wire=self.selections();items=wire['requirements'][self.slot['key']]['items']
            if change=='binding':wire['binding_ref']='other-task'
            elif change=='unknown_field':wire['anything']=[]
            elif change=='authority':items[0]['source_check']='通过'
            elif change=='source':items[0]['source_ref']='foreign-source'
            else:wire['selections']=[copy.deepcopy(items[0])]
            with self.subTest(change=change),self.assertRaises(ContractError):self.decode(wire)

    def test_duplicate_ids_empty_found_and_missing_reason_are_not_repaired(self):
        for change in ('ids','empty','reason'):
            wire=self.selections();response=wire['requirements'][self.slot['key']]
            if change=='ids':response['items'][1]['id']=response['items'][0]['id']
            elif change=='empty':response['items']=[]
            else:wire['requirements'][self.slot['key']]=dict(status='not_found',reason=' ')
            with self.subTest(change=change),self.assertRaises(ContractError):self.decode(wire)

    def test_per_item_decode_rejection_is_not_a_crash_or_implicit_approval(self):
        from researchhelper import output_schema as module
        wire=self.selections();items=wire['requirements'][self.slot['key']]['items']
        items[1]=copy.deepcopy(items[0]);items[1]['id']='semantically_invalid'
        strict=module._decode_original_selection_set
        def decode(*args,**kwargs):
            if args[2]['selections'][0]['id']=='semantically_invalid':raise ContractError('Synthetic local rejection')
            return strict(*args,**kwargs)
        with patch.object(module,'_decode_original_selection_set',side_effect=decode):result=self.decode(wire)
        self.assertEqual(len(result['evidence_specs']),1)
        self.assertEqual(result[ORIGINAL_SELECTION_REJECTIONS]['rejected_items'][0]['category'],'selection_decode_rejected')

    def test_diagnostics_cannot_change_hash_counts_flags_locations_or_retain_raw_values(self):
        original=self.decode()[ORIGINAL_SELECTION_REJECTIONS]
        self.assertEqual(validated_original_selection_rejections(original,self.schema),original)
        for change in ('hash','counts','approval','location','extra'):
            diagnostic=copy.deepcopy(original)
            if change=='hash':diagnostic['schema_hash']='foreign'
            elif change=='counts':diagnostic['accepted_items']=2
            elif change=='approval':diagnostic['evidence_approved']=True
            elif change=='location':diagnostic['rejected_items'][0]['location']='$.requirements.foreign.items[0]'
            else:diagnostic['raw_value']='not allowed'
            with self.subTest(change=change),self.assertRaises(ContractError):
                validated_original_selection_rejections(diagnostic,self.schema)

    def test_model_phase_retains_fixed_diagnostic_without_new_output_authority(self):
        decoded=self.decode()
        with tempfile.TemporaryDirectory(prefix='independent-original-',dir=ROOT/'result/acceptance') as folder:
            settings=ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash',api_key='SYNTHETIC_OFFLINE'))
            service=ModelService(settings,offline=False)
            with patch.object(service,'reason',return_value=json.dumps(decoded)):
                value,receipt=model_object(service,self.task,'extract',payload(self.task,self.views),PHASE_FIELDS['extract'])
        self.assertEqual(set(value),{'evidence_specs'})
        self.assertEqual(len(value['evidence_specs']),1)
        self.assertEqual(receipt['original_selection_rejections'],decoded[ORIGINAL_SELECTION_REJECTIONS])
        self.assertEqual(self.task['evidence'],fixtures.TypedOriginalRequirementTests.task['evidence'])

    def test_actual_responses_envelope_decodes_once_and_keeps_budget_and_diagnostic(self):
        before=copy.deepcopy(self.task['ledger'])
        response=dict(object='response',status='completed',error=None,incomplete_details=None,
            output=[dict(type='message',role='assistant',status='completed',
                content=[dict(type='output_text',text=json.dumps(self.selections()))])],usage={})
        with tempfile.TemporaryDirectory(prefix='independent-response-',dir=ROOT/'result/acceptance') as folder:
            settings=ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash',api_key='SYNTHETIC_OFFLINE_RESPONSE'))
            service=ModelService(settings,offline=False)
            with patch.object(service,'_request_bytes',return_value=json.dumps(response).encode()) as transport:
                value,receipt=model_object(service,self.task,'extract',payload(self.task,self.views),PHASE_FIELDS['extract'])
            transport.assert_called_once()
        self.assertEqual(set(value),{'evidence_specs'})
        self.assertEqual(len(value['evidence_specs']),1)
        self.assertEqual(receipt['original_selection_rejections']['accepted_items'],1)
        self.assertEqual(self.task['ledger']['used']['model_calls'],before['used']['model_calls']+1)
        self.assertEqual(self.task['ledger']['used']['data_requests'],before['used']['data_requests'])
        self.assertEqual(self.task['ledger']['limits'],before['limits'])

    def test_invalid_policy_versions_are_not_treated_as_enabled(self):
        for version in (True,0,2,'1'):
            self.task[POLICY]=version
            with self.subTest(version=version),self.assertRaises(ContractError):self.decode()

    def receipt(self):
        return dict(phase='extract',output_protocol='strict_original_spans_v1',
            task_id=self.task['id'],research_version=self.task['version'],batch=self.task['batch'],
            input_hash=self.task['input_hash'],output_schema_hash=fingerprint(self.schema),
            output_hash=fingerprint(self.decode()),original_selection_rejections=self.decode()[ORIGINAL_SELECTION_REJECTIONS])

    def test_issue_retention_is_idempotent_and_not_an_extra_required_obligation(self):
        agent=object.__new__(ResearchAgent);agent._save_model_checkpoint=Mock()
        receipt=self.receipt()
        before={key:copy.deepcopy(self.task[key]) for key in ('ledger','questions','evidence','gaps')}
        agent._retain_original_selection_diagnostics(self.task,receipt,'synthetic_input')
        agent._retain_original_selection_diagnostics(self.task,receipt,'synthetic_input')
        self.assertEqual(len(self.task['reasoning_state']['original_selection_rejections']),1)
        agent._save_model_checkpoint.assert_called_once()
        self.assertEqual(before,{key:self.task[key] for key in before})
        diagnostic=self.task['gap_records'][-1]
        self.assertFalse(diagnostic['required'])
        self.assertEqual(diagnostic['question_ids'],[])
        self.assertTrue(pending(self.task))  # Valid decoded specs were not adopted.

    def test_foreign_receipt_cannot_attach_diagnostic_to_another_task_or_version(self):
        receipt=self.receipt();agent=object.__new__(ResearchAgent);agent._save_model_checkpoint=Mock()
        for key in ('task_id','research_version','batch','input_hash','output_schema_hash'):
            changed=copy.deepcopy(receipt);changed[key]='foreign'
            before=fingerprint(self.task)
            with self.subTest(key=key),self.assertRaises(ContractError):
                agent._retain_original_selection_diagnostics(self.task,changed,'synthetic_input')
            self.assertEqual(before,fingerprint(self.task))
        agent._save_model_checkpoint.assert_not_called()

    def test_new_policy_is_bound_to_input_and_frozen_report_not_old_study(self):
        from researchhelper.archive import ResearchArchive
        before=input_fingerprint(self.task)
        # A new input hash must not inherit an old reading approval.
        with self.assertRaises(ContractError):compose(self.task)
        with tempfile.TemporaryDirectory(prefix='independent-new-task-',dir=ROOT/'result/acceptance') as folder:
            agent=ResearchAgent(ResearchArchive(folder),ROOT,offline=True,require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            fresh=agent.create('解释独立依据和辅助材料的研究边界',research_mode='current')
            self.assertEqual(fresh[POLICY],1)
            agent._adopt_method_knowledge(fresh)
            fresh['status']='部分完成'
            agent._gap(fresh,'离线契约测试未执行研究，不代表业务结果。',category='question_coverage')
            self.assertEqual(compose(fresh)[POLICY],1)
        old=copy.deepcopy(self.task);old.pop(POLICY)
        self.assertNotEqual(input_fingerprint(old),before)
        self.assertNotIn(POLICY,fixtures.TypedOriginalRequirementTests.task)


class RetainedMacroSelectorIsolationTests(unittest.TestCase):
    def test_actual_original_enums_accept_valid_synthetic_choice_but_not_foreign_quantity(self):
        from researchhelper.reasoning import extractable_source_views
        source=ROOT/'result/acceptance/one-sentence-research-lhsid82h/task.json'
        original=source.read_bytes();before=hashlib.sha256(original).hexdigest()
        task=json.loads(original);task[POLICY]=1;task['input_hash']=input_fingerprint(task)
        with patch('socket.socket.connect',side_effect=AssertionError('Offline original inspection')):
            views=extractable_source_views(task)
            schema=original_span_output_schema(task,views)
            self.assertEqual(schema['properties']['version']['enum'],[12])
            slots=requirement_slots(task)
            slot=next(s for s in slots if s['constraints']['value_kind']=='number')
            branch=schema['properties']['requirements']['properties'][slot['key']]['anyOf'][0]
            shape=branch['properties']['items']['items']['anyOf'][0]
            def literal(schema):
                if 'anyOf' in schema:return literal(schema['anyOf'][0])
                if 'enum' in schema:return schema['enum'][0]
                if schema['type']=='object':return {k:literal(v) for k,v in schema['properties'].items()}
                if schema['type']=='array':return []
                if schema['type']=='string':return 'synthetic_test_choice'
                raise AssertionError('Do not invent an unregistered reference shape')
            valid=literal(shape);valid['id']='macro_valid_choice'
            quantity=next(b for b in shape['properties']['number_reference']['anyOf']
                          if 'quantity_ref' in b.get('properties',{}))
            valid['number_reference']=literal(quantity)
            self.assertIn('quantity_ref',valid['number_reference'])
            invalid=copy.deepcopy(valid);invalid['id']='macro_invalid_choice'
            invalid['number_reference']['quantity_ref']='quantity_not_in_this_original'
            wire=dict(version=12,binding_ref=schema['properties']['binding_ref']['enum'][0],selections=[],
                requirements={s['key']:dict(status='not_found',reason='合成选择实验，不判断原件是否缺失') for s in slots})
            wire['requirements'][slot['key']]=dict(status='found',items=[valid,invalid])
            result=decode_original_spans(wire,schema,(task,views))
            self.assertEqual(len(result['evidence_specs']),1)
            self.assertEqual(result['evidence_specs'][0]['unit'],'%')
            self.assertEqual(result[ORIGINAL_SELECTION_REJECTIONS]['accepted_items'],1)
            self.assertFalse(result[ORIGINAL_SELECTION_REJECTIONS]['evidence_approved'])
            self.assertEqual(task['evidence'],[])  # No adoption, replayed model output or report.
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)


if __name__=='__main__':unittest.main()
