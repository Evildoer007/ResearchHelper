"""Only new research carries the factual-versus-investment scope contract."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.planning import input_fingerprint, planning_scope_policy
from researchhelper.reports import compose

ROOT = Path(__file__).resolve().parents[1]


class ReportScopeWiringTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(Path(folder.name)), ROOT, offline=True,
                                   require_argument_support=True)
        guard = patch('socket.socket.connect', side_effect=AssertionError('No external calls'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_new_research_binds_scope_policy(self):
        task = self.agent.create('核对营业收入并计算净利润率，生成研究报告', entry='个股')
        self.assertEqual(task['planning_scope_version'], 1)
        self.assertIsNotNone(planning_scope_policy(task))
        legacy = copy.deepcopy(task)
        legacy.pop('planning_scope_version')
        self.assertNotEqual(input_fingerprint(task), input_fingerprint(legacy))

    def test_report_freezes_policy_without_rewriting_legacy(self):
        task = self.agent.create('核对营业收入', entry='个股')
        task['knowledge'] = ResearchKnowledge().read(task['entry'], task['question'], context=task)
        task['status'] = '部分完成'
        task['gaps'] = ['离线契约检查，尚未执行取数和分析。']
        original = copy.deepcopy(task)
        content = compose(task)
        self.assertEqual(content['planning_scope_version'], 1)
        self.assertEqual(task, original)
        legacy = copy.deepcopy(task)
        legacy.pop('planning_scope_version')
        before = copy.deepcopy(legacy)
        self.assertNotIn('planning_scope_version', compose(legacy))
        self.assertEqual(legacy, before)


if __name__ == '__main__':
    unittest.main()
