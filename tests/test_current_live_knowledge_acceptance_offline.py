"""Independent checker and single-use live guard, entirely offline."""
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from core.bootstrap import configure_imports
configure_imports()
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from tests import accept_current_live_knowledge_research as probe

ROOT = Path(__file__).resolve().parents[1]


class CurrentKnowledgeAcceptanceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='knowledge-checker-', dir=ROOT / 'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        network = patch.object(socket.socket, 'connect', side_effect=AssertionError('External network forbidden'))
        network.start()
        self.addCleanup(network.stop)
        agent = ResearchAgent(ResearchArchive(self.folder / 'store'), ROOT, offline=True)
        task = agent.create(probe.QUESTION, entry='ETF', questions=[{
            'id': 'q1', 'kind': 'fact', 'question': probe.QUESTION,
            'answer_scope': 'general_definition', 'knowledge_needs': ['rh_k_etf_measures']}])
        self.task = agent.start(task['id'])
        self.report = agent.export(task['id'])['html']

    def test_verified_definition_is_not_investment_quality_approval(self):
        result = probe.verify_definition(self.task, self.report)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)
        self.assertEqual(result['public_source_count'], 2)
        self.assertFalse(result['independent_investment_quality'] or result['market_value_approval'])

    def test_appended_knowledge_cannot_hide_an_incomplete_required_answer(self):
        changed = copy.deepcopy(self.task)
        changed['questions'][0]['answer'] = '市场价格不同于净值。'
        with self.assertRaises(AssertionError):
            probe.verify_definition(changed, self.report)

    def test_forged_versioned_knowledge_is_not_accepted(self):
        changed = copy.deepcopy(self.task)
        entry = next(item for item in changed['knowledge']['entries'] if item['id'] == 'rh_k_etf_measures')
        entry['content'] += '保证未来盈利。'
        with self.assertRaises(AssertionError):
            probe.verify_definition(changed, self.report)

    def test_missing_source_link_fails_even_with_complete_text(self):
        entry = next(item for item in self.task['knowledge']['entries'] if item['id'] == 'rh_k_etf_measures')
        report = self.report.replace('href="' + entry['source_reference']['url'] + '"', 'href="#"')
        with self.assertRaises(AssertionError):
            probe.verify_definition(self.task, report)

    def test_financial_evidence_or_changed_input_cannot_pass_this_scope(self):
        for changes in ({'evidence': [{'id': 'unverified'}]}, {'question': '推荐一只基金'}, {'questions': []}):
            with self.subTest(keys=list(changes)), self.assertRaises(AssertionError):
                probe.verify_definition(copy.deepcopy(self.task) | changes, self.report)

    def test_missing_explicit_scope_never_dispatches(self):
        with patch.dict('os.environ', {}, clear=True), self.assertRaises(RuntimeError):
            probe.selected_candidate()

    def test_same_case_cannot_be_reopened_under_another_candidate(self):
        closed = self.folder / 'result/acceptance/live-knowledge-research-closed'
        closed.mkdir(parents=True)
        (closed / 'receipt.json').write_text(json.dumps({'case': probe.CASE, 'status': 'failed'}))
        with patch.object(probe, 'ROOT', self.folder), patch.dict('os.environ', {
            'RESEARCHHELPER_AUTHORIZED_LIVE_CASE': probe.AUTHORIZATION,
            'RESEARCHHELPER_CURRENT_RECEIPT': str(self.folder / 'new-candidate.json')}, clear=True), self.assertRaises(RuntimeError):
            probe.selected_candidate()


if __name__ == '__main__':
    unittest.main()
