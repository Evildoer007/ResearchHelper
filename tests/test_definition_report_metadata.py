"""A frozen report must retain the definition projection's protocol version."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent


class DefinitionReportMetadataTests(unittest.TestCase):
    def test_frozen_content_preserves_definition_projection_version(self):
        with tempfile.TemporaryDirectory(prefix='definition-freeze-', dir=ROOT / 'result/acceptance') as folder, \
                patch('socket.socket.connect', side_effect=AssertionError('No network')):
            agent = ResearchAgent(ResearchArchive(Path(folder) / 'store'), ROOT,
                                  offline=True, require_argument_support=True)
            task = agent.create('解释价格变化与总收益，不查询当前数值', entry='个股', questions=[
                dict(id='q1', kind='fact', question='价格变化与总收益的区别',
                     answer_scope='general_definition', knowledge_needs=['rh_k_price_return_measures'])])
            done = agent.start(task['id'])
            self.assertEqual(done['status'], '已完成', done.get('error'))
            content = agent.export(task['id'])['content']
            self.assertEqual(content.get('definition_projection_version'), done['definition_projection_version'])
            self.assertEqual(content['questions'][0]['knowledge_integrity'], done['questions'][0]['knowledge_integrity'])
