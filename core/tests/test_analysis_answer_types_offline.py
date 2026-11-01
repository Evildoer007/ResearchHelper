"""Expose answer types and retain strict rejection without storing bad content."""

import copy
import json
import unittest

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.reasoning import (ANALYSIS_TEXT_RULES_VERSION, PHASE_FIELDS, PHASE_RULES,
    RULES_VERSION, analysis_contract, model_object, phase_output_example, validate_analysis_output)
from tests import test_analysis_output_structure_offline as fixtures


class AnalysisAnswerTypesTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AnalysisOutputStructureTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = self.fixture.task

    def test_contract_and_prompt_keep_text_separate_from_numeric_segments(self):
        before = fingerprint(self.task)
        item = analysis_contract(self.task)['answer_item_structure']
        self.assertEqual(item['answer']['type'], 'string')
        self.assertEqual(item['version'], ANALYSIS_TEXT_RULES_VERSION)
        self.assertTrue(item['answer']['optional_with_numeric_segments'])
        self.assertEqual(item['numeric_segments']['answer']['type'], 'array')
        self.assertIn('不放进answer字段', PHASE_RULES['analyze'])
        self.assertEqual(fingerprint(self.task), before)

    def test_invalid_text_types_have_safe_diagnostics_and_no_coercion(self):
        for value, kind in ((['DO_NOT_STORE'], 'array'), ({'DO_NOT_STORE': 1}, 'object'),
                            (None, 'null'), (True, 'boolean'), (3, 'number')):
            with self.subTest(kind=kind):
                before = len(self.task['ledger']['dispatches'])
                self.task['ledger']['dispatches'].append({'kind': 'model_calls', 'status': '成功'})
                proposal = {'answers': {'DO_NOT_STORE': {'answer': value}}, 'theses': []}
                with self.assertRaises(ContractError):
                    validate_analysis_output(None, self.task, proposal, before)
                diagnostic = self.task['ledger']['dispatches'][-1]['output_contract']['item_structure']
                self.assertEqual(diagnostic['invalid_fields'], ['answers.*.answer'])
                self.assertEqual(diagnostic['text_type_mismatches'],
                                 [{'path': 'answers.*.answer', 'expected': 'string', 'actual': kind}])
                self.assertNotIn('DO_NOT_STORE', json.dumps(self.task))

    def test_segments_only_proposal_remains_structurally_valid_and_unapproved(self):
        before = copy.deepcopy(self.task)
        validate_analysis_output(None, self.task, {'answers': {'q1': {
            'numeric_segments': {'answer': [{'text': '仅为结构检查'}]},
            'refs': [], 'calculation_refs': []}}, 'theses': []}, len(self.task['ledger']['dispatches']))
        self.assertEqual(self.task, before)
        self.assertFalse(self.task['evidence'])

    def test_analysis_text_version_does_not_change_other_phase_protocols(self):
        self.assertIs(type(RULES_VERSION), int)
        self.assertGreater(RULES_VERSION, 0)
        for phase in PHASE_FIELDS:
            with self.subTest(phase=phase):
                model = self.fixture.model(phase_output_example(phase))
                _, receipt = model_object(model, self.task, phase, {}, PHASE_FIELDS[phase])
                self.assertEqual(receipt['rules_version'], RULES_VERSION)
                if phase == 'analyze':
                    self.assertEqual(receipt['analysis_text_rules_version'], ANALYSIS_TEXT_RULES_VERSION)
                else:
                    self.assertNotIn('analysis_text_rules_version', receipt)


if __name__ == '__main__':
    unittest.main()
