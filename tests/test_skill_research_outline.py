"""The actual host entry exposes the same guide as App analysis inputs."""
from pathlib import Path
import tempfile
import unittest

from core.session import Session
from researchhelper.planning import research_outline_contract


class SkillResearchOutlineTests(unittest.TestCase):
    def test_host_tools_and_analysis_share_one_bound_research_outline(self):
        with tempfile.TemporaryDirectory(prefix='skill-outline-project-') as folder:
            session = Session(Path(folder), allow_network=False)
            task = session.create('研究600519.SH的经营驱动和投资逻辑')
            tools = session.tools(task['id'])
            expected = research_outline_contract(task)
            self.assertIsNotNone(expected)
            self.assertEqual(tools['research_outline'], expected)
            self.assertEqual(tools['analysis']['client_writing']['research_outline'], expected)
            latest = session.archive.get(task['id'])
            self.assertEqual(latest['ledger']['used']['model_calls'], 0)
            self.assertEqual(latest['ledger']['used']['data_requests'], 0)


if __name__ == '__main__':
    unittest.main()
