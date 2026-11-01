"""Concrete answer examples are source-bound shapes, never repaired answers."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.numeric_integrity import bind_numeric_statement, numeric_segment_shape_errors
from researchhelper.reasoning import analysis_contract

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / 'result/acceptance/default-app-investment-kzsle2lo/task.json'


class CurrentAnalysisShapeExampleTests(unittest.TestCase):
    def setUp(self):
        self.digest = hashlib.sha256(ORIGINAL.read_bytes()).hexdigest()
        self.task = json.loads(ORIGINAL.read_text())
        guard = patch('socket.socket.connect', side_effect=AssertionError('No network'))
        guard.start(); self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(ORIGINAL.read_bytes()).hexdigest(), self.digest)

    def test_example_has_exact_business_container_without_values_or_extra_keys(self):
        before = fingerprint(self.task)
        example = analysis_contract(self.task)['numeric_statement']['answer_shape_example'][0]
        self.assertTrue(example['format_only'])
        self.assertFalse(example['automatic_adoption'])
        self.assertEqual(numeric_segment_shape_errors(example['answer_item']['numeric_segments'], ['answer']), [])
        quantity = example['answer_item']['numeric_segments']['answer'][1]
        self.assertEqual(set(quantity), {'source', 'ref', 'path', 'format', 'digits'})
        self.assertEqual(fingerprint(self.task), before)

    def test_example_quantity_uses_actual_question_reference_and_checked_raw_number(self):
        contract = analysis_contract(self.task)
        example = contract['numeric_statement']['answer_shape_example'][0]
        item = dict(copy.deepcopy(example['answer_item']), question_ids=[example['question_id']])
        bound = bind_numeric_statement(self.task, item, ['answer'], item['refs'], item['calculation_refs'])
        self.assertTrue(bound['fields']['answer'])
        self.assertIn(item['numeric_segments']['answer'][1], contract['by_question'][example['question_id']]['numeric_quantity_references'])

    def test_qualitative_only_research_never_gets_fake_quantity_example(self):
        self.task['evidence'] = [item for item in self.task['evidence'] if item.get('value') is None]
        self.assertEqual(analysis_contract(self.task)['numeric_statement']['answer_shape_example'], [])

    def test_extra_numeric_segment_fields_are_still_rejected_not_coerced(self):
        example = analysis_contract(self.task)['numeric_statement']['answer_shape_example'][0]
        item = dict(copy.deepcopy(example['answer_item']), question_ids=[example['question_id']])
        item['numeric_segments']['answer'][1]['type'] = 'number'
        with self.assertRaises(ContractError):
            bind_numeric_statement(self.task, item, ['answer'], item['refs'], item['calculation_refs'])

    def test_shape_example_does_not_satisfy_all_evidence_obligations(self):
        example = analysis_contract(self.task)['numeric_statement']['answer_shape_example'][0]
        self.assertEqual(len(example['answer_item']['refs']), 1)
        self.assertEqual(len(self.task['questions'][0]['evidence_needs']), 5)
        self.assertNotEqual(self.task['questions'][0]['status'], '已回答')


if __name__ == '__main__': unittest.main()
