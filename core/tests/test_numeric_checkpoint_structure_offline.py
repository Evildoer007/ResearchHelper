"""Nested numeric proposals are checked before caching, without real inference."""

import copy
import json
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from researchhelper.reasoning import validate_analysis_output
from tests import test_autonomous_reasoning_offline as fixtures


class NumericCheckpointStructureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AutonomousReasoningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        guard = patch('socket.socket.connect', side_effect=AssertionError('External calls forbidden'))
        guard.start();self.addCleanup(guard.stop)
        self.task = self.fixture.policy()

    def test_invalid_nested_shapes_are_rejected_without_task_mutation(self):
        before = copy.deepcopy(self.task)
        quantity = {'source': 'evidence', 'ref': 'e1', 'path': 'value', 'format': 'signed', 'digits': 2}
        bad = [None, [], {'answer': []}, {'answer': ['DO_NOT_CACHE']}, {'answer': [{'text': 3}]},
               {'unknown': [{'text': 'DO_NOT_CACHE'}]},
               *({'answer': [dict(quantity, **{key: value})]} for key, value in
                 [('digits', True), ('digits', 9), ('format', []), ('ref', []), ('source', 'memory'), ('path', 'unregistered')])]
        for segments in bad:
            proposal = {'answers': {'q1': {'numeric_segments': segments, 'refs': []}}, 'theses': []}
            with self.subTest(segments=segments), self.assertRaises(ContractError):
                validate_analysis_output(None, self.task, proposal, len(self.task['ledger']['dispatches']))
            self.assertEqual(self.task, before)

    def test_bad_model_numeric_shape_is_not_saved_and_explicit_resume_can_finish(self):
        bad = {'answers': {'q1': {'numeric_segments': {'answer': ['DO_NOT_CACHE']}, 'refs': ['e1']}}, 'theses': []}
        model = fixtures.FabricatedModel([self.fixture.plan(self.task),
            {'evidence_specs': [self.fixture.policy_spec(self.task)]}, bad])
        with patch('researchhelper.model.urllib.request.urlopen', side_effect=model.respond):
            failed = self.fixture.agent.start(self.task['id'])
        self.assertEqual(failed['status'], '失败')
        self.assertEqual(len(model.requests), 3)
        self.assertNotIn('DO_NOT_CACHE', json.dumps(failed))
        self.assertNotIn('input_digest', failed.get('reasoning_state', {}).get('analysis', {}))
        diagnostic = failed['ledger']['dispatches'][-1]['output_contract']['item_structure']
        self.assertEqual(diagnostic['invalid_fields'], ['answers.*.numeric_segments.*.*'])
        recovered = fixtures.FabricatedModel([self.fixture.answer()])
        with patch('researchhelper.model.urllib.request.urlopen', side_effect=recovered.respond):
            final = self.fixture.agent.start(self.task['id'])
        self.assertEqual(final['status'], '已完成', final.get('error'))
        self.assertEqual(len(recovered.requests), 1)
        self.assertEqual(final['ledger']['used']['model_calls'], failed['ledger']['used']['model_calls'] + 1)
        self.assertIsNotNone(final['delivery'])

    def test_structural_acceptance_does_not_approve_a_reference(self):
        proposal = {'answers': {'q1': {'numeric_segments': {'answer': [
            {'source': 'evidence', 'ref': 'unapproved', 'path': 'value', 'format': 'signed', 'digits': 2}]},
            'refs': ['unapproved']}}, 'theses': []}
        before = copy.deepcopy(self.task)
        validate_analysis_output(None, self.task, proposal, len(self.task['ledger']['dispatches']))
        self.assertEqual(self.task, before)
        self.assertFalse(self.task['evidence'])

    def test_host_invalid_numeric_shape_is_rejected_before_spending_or_caching(self):
        before = self.fixture.agent.archive.get(self.task['id'])
        proposals = [
            {'answers': {'q1': {'numeric_segments': {'answer': ['DO_NOT_CACHE']}}}},
            {'theses': [{'numeric_segments': {'judgment': [{'text': 'DO_NOT_CACHE'}]}}]},
        ]
        for proposal in proposals:
            with self.subTest(proposal=proposal), self.assertRaises(ContractError):
                self.fixture.agent.start(self.task['id'], proposal)
            self.assertEqual(self.fixture.agent.archive.get(self.task['id']), before)


if __name__ == '__main__':
    unittest.main()
