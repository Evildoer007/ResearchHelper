"""Forensic node replay: real proposals, actual adoption and calculation gates.

No live study is resumed. Transport/extraction alone are replaced by the
already-recorded outputs; no number, link, review or requirement is repaired.
"""
import copy
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent

SOURCE = ROOT/'result/acceptance/one-sentence-research-o9nv4a91/task.json'


class FinancialProgressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = SOURCE.read_bytes()
        cls.original = json.loads(cls.raw)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='financial-node-replay-', dir=ROOT/'result/acceptance')
        self.addCleanup(self.folder.cleanup)
        self.model = Mock()
        self.model.reason.side_effect = AssertionError('No live model in forensic replay')
        self.agent = ResearchAgent(ResearchArchive(self.folder.name), ROOT, offline=False,
            model=self.model, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = copy.deepcopy(self.original)
        self.task.update(status='运行中', delivery=None, revision=0)
        self.agent.archive.save(self.task)
        self.proposed = copy.deepcopy(self.task['host_result'])
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No live network'))
        guard.start(); self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).digest(), hashlib.sha256(self.raw).digest())

    def adopt(self):
        with patch.object(self.agent, '_acquire_sources') as acquire, \
             patch('researchhelper.acquisition.complete_initial_acquisition') as complete, \
             patch.object(self.agent, '_extract_model_sources', return_value=self.proposed) as extract:
            self.agent._step(self.task, 'evidence', None)
        acquire.assert_called_once()
        complete.assert_called_once()
        extract.assert_called_once()

    def test_evidence_node_adopts_the_same_three_amounts_without_rewriting_proposals(self):
        before = copy.deepcopy(self.task['ledger'])
        self.adopt()
        amounts = {e['calculation_context']['metric']: e['raw_value'] for e in self.task['evidence']
                   if e.get('calculation_context', {}).get('metric') in
                   ('revenue', 'net_profit', 'operating_cash_flow')}
        self.assertEqual(amounts, dict(revenue=276916580, net_profit=47030638, operating_cash_flow=60216851))
        self.assertEqual(self.task['ledger'], before)
        self.assertEqual(self.proposed, self.original['host_result'])

    def test_calculation_node_reaches_both_registered_ratios_without_model_binding(self):
        self.adopt()
        before = copy.deepcopy(self.task['ledger'])
        self.agent._step(self.task, 'calculation', None)
        self.assertEqual({item['method'] for item in self.task['calculations']},
                         {'net_profit_margin_v1', 'operating_cash_profit_ratio_v1'})
        self.assertEqual(self.task['ledger'], before)
        self.model.reason.assert_not_called()

    def test_analysis_node_receives_the_actual_inputs_and_ratios_despite_unfilled_metadata_need(self):
        class CapturedAnalysis(Exception):
            pass
        self.adopt()
        self.agent._step(self.task, 'calculation', None)
        before = copy.deepcopy(self.task['ledger'])
        node_id = next(node['id'] for node in self.task['nodes']
                       if node.get('kind', node['id']) in ('answer', 'thesis'))
        with patch('researchhelper.engine.model_object', side_effect=CapturedAnalysis) as transport:
            with self.assertRaises(CapturedAnalysis):
                self.agent._step(self.task, node_id, None)
        self.assertEqual(transport.call_count, 1)
        args = transport.call_args.args
        self.assertEqual(args[2], 'analyze')
        payload = args[3]
        self.assertEqual({item['method'] for item in payload['calculations']},
                         {'net_profit_margin_v1', 'operating_cash_profit_ratio_v1'})
        self.assertTrue(payload['gaps'])
        self.assertEqual(self.task['ledger'], before)
        self.assertFalse(self.task.get('argument_support_reviews'))


if __name__ == '__main__':
    unittest.main()
