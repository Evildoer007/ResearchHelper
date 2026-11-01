"""Late executable bindings do not erase the immutable acquisition scope."""
import copy
import hashlib
import json
from pathlib import Path
import unittest

from researchhelper.contracts import fingerprint
from researchhelper.planning import input_fingerprint
from researchhelper.evidence import identity_resolutions,current_original_reference,validate_current_snapshot
from researchhelper.engine import ResearchAgent

ROOT=Path(__file__).resolve().parents[1]
RETAINED=ROOT/'result/acceptance/one-sentence-research-e9v0abvi/task.json'


class DerivedCalculationInputBindingTests(unittest.TestCase):
    def setUp(self):
        self.digest=hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        self.task=json.loads(RETAINED.read_text())
        self.base=copy.deepcopy(self.task);self.base['calculation_specs']=[]
        self.base['reasoning_state'].pop('initial_calculation_binding',None)
        self.assertEqual(input_fingerprint(self.base),self.task['input_hash'])
        state=self.task['reasoning_state']['initial_calculation_binding']
        state.update(derived_plan_version=1,input_base_hash=self.task['input_hash'],input_calculation_specs=[],
            registered_plan_hash=fingerprint(self.task['calculation_specs']))

    def tearDown(self):
        self.assertEqual(hashlib.sha256(RETAINED.read_bytes()).hexdigest(),self.digest)

    def test_new_registered_bindings_keep_input_hash_and_identity_valid(self):
        self.assertEqual(input_fingerprint(self.task),self.task['input_hash'])
        self.assertEqual(identity_resolutions(self.task)[0]['status'],'代码映射已核验')
        record=next(record for record in self.task['source_records'] if record['kind']=='filing')
        self.assertIsNotNone(current_original_reference(self.task,record))
        for item in self.task['evidence']:self.assertTrue(validate_current_snapshot(item,self.task))

    def test_verified_material_projection_remains_available_after_binding(self):
        payload=ResearchAgent._reasoning_evidence_payload(self.task)
        self.assertEqual(len(payload['evidence']),len(self.task['evidence']))
        self.assertEqual(len(self.task['calculations']),2)

    def test_modified_formula_cannot_hide_behind_registered_extension(self):
        self.task['calculation_specs'][0]['parameters']['currency']='USD'
        self.assertNotEqual(input_fingerprint(self.task),self.task['input_hash'])
        self.assertEqual(identity_resolutions(self.task)[0]['status'],'待核验')

    def test_changed_question_or_fake_base_hash_cannot_reuse_the_receipt(self):
        for change in ('question','base_hash'):
            task=copy.deepcopy(self.task)
            if change=='question':task['question']='另一个研究对象'
            else:task['reasoning_state']['initial_calculation_binding']['input_base_hash']='forged'
            self.assertNotEqual(input_fingerprint(task),task['input_hash'])

    def test_legacy_tasks_keep_the_original_fingerprint_algorithm(self):
        self.assertEqual(input_fingerprint(self.base),self.base['input_hash'])
