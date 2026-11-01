"""Completion failures in the real research flow, with fabricated transport."""
import copy
import io
import json
import socket
import unittest
from unittest.mock import patch

from tests import test_autonomous_reasoning_offline as fixtures


class ModelCompletionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AutonomousReasoningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        network = patch.object(socket.socket, 'connect', side_effect=AssertionError('No external connections'))
        network.start()
        self.addCleanup(network.stop)

    def interrupted(self, step, finish):
        task = self.fixture.policy()
        model = fixtures.FabricatedModel([self.fixture.plan(task),
                                 {'evidence_specs': [self.fixture.policy_spec(task)]},
                                 self.fixture.answer()])
        def response(request, timeout):
            original = model.respond(request, timeout)
            try:
                value = json.loads(original.read())
            finally:
                original.close()
            value['choices'][0]['finish_reason'] = finish if len(model.requests) == step else 'stop'
            return io.BytesIO(json.dumps(value).encode())
        original_materials = copy.deepcopy(task['materials'])
        with patch('researchhelper.model.urllib.request.urlopen', side_effect=response) as transport:
            final = self.fixture.agent.start(task['id'])
        self.assertEqual(transport.call_count, step)
        self.assertEqual(final['ledger']['used']['model_calls'], step)
        self.assertEqual(final['ledger']['used']['data_requests'], 0)
        self.assertEqual(final['materials'], original_materials)
        self.assertEqual(final['theses'], [])
        self.assertEqual(final['ledger']['dispatches'][-1]['status'], '失败')
        return final

    def test_interrupted_plan_is_not_adopted_or_retried(self):
        final = self.interrupted(1, 'aborted')
        self.assertEqual(final['status'], '失败')
        self.assertFalse(final['evidence'] or final['calculations'])
        self.assertIsNone(final['delivery'])
        self.assertEqual(final['ledger']['dispatches'][-1]['failure_code'], 'generation_interrupted')

    def test_interrupted_extract_saves_only_restricted_delivery(self):
        final = self.interrupted(2, 'insufficient_system_resource')
        self.assertEqual(final['status'], '部分完成')
        self.assertFalse(final['evidence'] or final['calculations'])
        self.assertTrue(final['delivery'])
        self.assertIn('受限材料报告', self.fixture.agent.export(final['id'])['html'])

    def test_unfinished_answer_preserves_evidence_without_promoting_answer(self):
        final = self.interrupted(3, None)
        self.assertEqual(final['status'], '部分完成')
        self.assertEqual(len(final['evidence']), 1)
        self.assertNotEqual(final['questions'][0]['status'], '已回答')
        self.assertTrue(final['delivery'])
        self.assertEqual(final['ledger']['dispatches'][-1]['failure_code'], 'completion_not_confirmed')


if __name__ == '__main__':
    unittest.main()
