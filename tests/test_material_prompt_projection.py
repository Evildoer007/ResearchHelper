"""Readonly original-text prompt checks using retained real and synthetic data.

No new model/provider request, approval, production-store write or amendment of
the saved failed study. Prompt size alone is not financial-quality acceptance.
"""
import copy
import hashlib
import json
from pathlib import Path
import unittest

from researchhelper.contracts import canonical,fingerprint
from researchhelper.planning import question_specs
from researchhelper.reasoning import evidence_contract,model_object
import researchhelper.reasoning as reasoning

ROOT=Path(__file__).resolve().parents[1]
RETAINED=ROOT/'result/acceptance/default-app-investment-zmljdjci/task.json'


def retained_payload():
    task=json.loads(RETAINED.read_text())
    bundle=task['reasoning_state']['reading']['bundle'];views=bundle['views']
    contract=evidence_contract(task,views)
    payload=dict(question=task['question'],intent=task['intent'],objects=task['objects'],cutoff=task['cutoff'],
        questions=question_specs(task),calculations=task['calculation_specs'],evidence_contract=contract,
        field_registry={},untrusted_sources=views,
        source_reading={key:bundle[key] for key in ('reading_hash','coverage','limitations')},
        calculation_input_targets=copy.deepcopy(contract['calculation_input_requirements']['proposal_requirements']))
    return task,payload


class RecordingModel:
    def __init__(self):self.messages=None
    def reason_phase(self,task,messages,phase):
        self.messages=copy.deepcopy(messages)
        return json.dumps({'evidence_specs':[]})


def prompt_statistics():
    task,payload=retained_payload();model=RecordingModel()
    model_object(model,task,'extract',payload,{'evidence_specs'})
    body=json.loads(model.messages[1]['content'])
    return dict(input_characters=len(canonical(payload)),presented_characters=len(canonical(body)),
        system_characters=len(model.messages[0]['content']),
        payload_sections={key:len(canonical(value)) for key,value in payload.items()},
        contract_sections={key:len(canonical(value)) for key,value in payload['evidence_contract'].items()},
        retained_source_sha256=hashlib.sha256(RETAINED.read_bytes()).hexdigest(),
        model_calls=0,provider_calls=0)


class MaterialPromptProjectionTests(unittest.TestCase):
    def setUp(self):
        self.task,self.payload=retained_payload()
        self.original_hash=hashlib.sha256(RETAINED.read_bytes()).hexdigest()

    def tearDown(self):
        self.assertEqual(hashlib.sha256(RETAINED.read_bytes()).hexdigest(),self.original_hash)

    def test_all_material_batch_preserves_every_original_character_and_binding(self):
        task_before=copy.deepcopy(self.task);before=copy.deepcopy(self.payload)
        projected,applied=reasoning.material_prompt_projection(self.payload)
        self.assertTrue(applied)
        for name in ('untrusted_sources','questions','source_reading','calculation_input_targets'):
            self.assertEqual(projected[name],before[name])
        for name in ('binding','sources','research_scope','question_requirements','object_binding_contract'):
            self.assertEqual(projected['evidence_contract'][name],before['evidence_contract'][name])
        self.assertEqual(self.task,task_before);self.assertEqual(self.payload,before)
        self.assertLess(len(canonical(projected)),len(canonical(before)))

    def test_api_only_sections_are_absent_without_removing_material_conditions(self):
        projected,applied=reasoning.material_prompt_projection(self.payload)
        self.assertTrue(applied)
        contract=projected['evidence_contract']
        for name in ('api_candidate_contract','api_row_groups','compact_api_context','compact_api_value'):
            self.assertNotIn(name,contract)
        for name in ('material_numeric_contract','material_text_contract','calculation_context_shape',
                     'calculation_unit_rule','current_original_rule','publication_reference_contract',
                     'question_coverage_contract','document_coverage_contract','review_types'):
            self.assertEqual(contract[name],self.payload['evidence_contract'][name])
        self.assertEqual(contract['locators']['material'],self.payload['evidence_contract']['locators']['material'])

    def test_unknown_publication_and_actual_original_references_stay_unchanged(self):
        projected,_=reasoning.material_prompt_projection(self.payload)
        original=self.payload['evidence_contract']
        for name in ('current_publication_template','current_availability_rule','sources',
                     'publication_types','unusable_rule','template_is_approval'):
            self.assertEqual(projected['evidence_contract'][name],original[name])
        self.assertIsNone(projected['evidence_contract']['current_publication_template']['at'])
        self.assertIs(projected['evidence_contract']['template_is_approval'],False)

    def test_duplicate_bound_inputs_are_referenced_once_not_replaced_with_defaults(self):
        payload=copy.deepcopy(self.payload)
        target=[{'id':'synthetic_input','required_context':['entity_id','period','currency']}]
        payload['calculation_input_targets']=target
        payload['evidence_contract']['calculation_input_requirements']['proposal_requirements']=copy.deepcopy(target)
        projected,_=reasoning.material_prompt_projection(payload)
        self.assertEqual(projected['calculation_input_targets'],target)
        self.assertNotIn('proposal_requirements',projected['evidence_contract']['calculation_input_requirements'])
        payload['evidence_contract']['calculation_input_requirements']['proposal_requirements'][0]['id']='other_input'
        projected,_=reasoning.material_prompt_projection(payload)
        self.assertEqual(projected['evidence_contract']['calculation_input_requirements']['proposal_requirements'],
                         payload['evidence_contract']['calculation_input_requirements']['proposal_requirements'])

    def test_mixed_missing_changed_or_duplicate_sources_keep_full_grammar(self):
        changes=(lambda p:p['untrusted_sources'][0].update(kind='api'),
            lambda p:p['evidence_contract']['sources'][0].update(snapshot_hash='changed'),
            lambda p:p['evidence_contract'].update(source_bindings_omitted=1),
            lambda p:p['evidence_contract']['sources'].append(copy.deepcopy(p['evidence_contract']['sources'][0])),
            lambda p:p['untrusted_sources'][0].update(parts=[]),
            lambda p:p.update(untrusted_sources=[]))
        for change in changes:
            payload=copy.deepcopy(self.payload);change(payload);before=copy.deepcopy(payload)
            projected,applied=reasoning.material_prompt_projection(payload)
            self.assertFalse(applied);self.assertIs(projected,payload);self.assertEqual(payload,before)

    def test_normal_phase_uses_material_guidance_and_retains_both_input_hashes(self):
        model=RecordingModel();task_before=copy.deepcopy(self.task)
        result,receipt=model_object(model,self.task,'extract',self.payload,{'evidence_specs'})
        presented=json.loads(model.messages[1]['content'])
        self.assertEqual(result,{'evidence_specs':[]})
        self.assertTrue(reasoning.MATERIAL_EXTRACTION_RULE in model.messages[0]['content'])
        self.assertEqual(receipt['material_prompt_version'],1)
        self.assertNotIn('extraction_prompt_version',receipt)
        self.assertEqual(receipt['context_hash'],fingerprint(self.payload))
        self.assertEqual(receipt['prompt_context_hash'],fingerprint(presented))
        self.assertEqual(self.task,task_before)

    def test_strict_provider_keeps_existing_schema_phase_path(self):
        model=RecordingModel();model.supports_structured_analysis=lambda:True
        _,receipt=model_object(model,self.task,'extract',self.payload,{'evidence_specs'})
        self.assertEqual(fingerprint(json.loads(model.messages[1]['content'])),fingerprint(self.payload))
        self.assertNotIn('material_prompt_version',receipt)

    def test_historical_research_does_not_gain_current_version_availability(self):
        task=copy.deepcopy(self.task);task['research_mode']='historical'
        payload=copy.deepcopy(self.payload)
        payload['evidence_contract']=evidence_contract(task,payload['untrusted_sources'])
        projected,applied=reasoning.material_prompt_projection(payload)
        self.assertTrue(applied)
        self.assertEqual(projected['evidence_contract']['research_scope'],payload['evidence_contract']['research_scope'])
        self.assertEqual(projected['evidence_contract']['sources'],payload['evidence_contract']['sources'])
        self.assertTrue(all('current_availability_reference' not in source
                            for source in projected['evidence_contract']['sources']))

    def test_prompt_size_reduction_keeps_all_negative_and_exception_spans(self):
        model=RecordingModel();model_object(model,self.task,'extract',self.payload,{'evidence_specs'})
        presented=json.loads(model.messages[1]['content'])
        original_size=len(canonical(self.payload))
        self.assertLess(len(canonical(presented)),original_size*.95)
        self.assertEqual(presented['source_reading'],self.payload['source_reading'])
        self.assertEqual(presented['untrusted_sources'],self.payload['untrusted_sources'])
        self.assertEqual(len(presented['untrusted_sources'][0]['parts']),47)


if __name__=='__main__':print(json.dumps(prompt_statistics(),ensure_ascii=False,indent=2))
