"""The diagnostic must create the same enabled policies as ordinary App tasks."""
from pathlib import Path
import tempfile
import unittest

from tests.probe_multitype_acquisition_current import diagnostic_agent
from researchhelper.archive import ResearchArchive
from researchhelper.evidence import validate_source_request
from researchhelper.index_sources import OUTPUT, PROFILES


class MatrixAppPolicyTests(unittest.TestCase):
    def test_discovery_and_index_are_enabled_without_mutating_task_policies(self):
        with tempfile.TemporaryDirectory() as directory:
            agent=diagnostic_agent(ResearchArchive(Path(directory)))
            try:
                task=agent.create('查询证券行业、算力产业链和上证50成分名单',entry='混合',objects=['000016.SH'],research_mode='current')
                self.assertEqual(task['object_discovery_version'],2)
                self.assertEqual(task['draft_first_version'],1)
                self.assertEqual(task['hk_code_alias_version'],1)
                self.assertEqual(task['index_constituent_source_version'],1)
                for query in ('证券行业股票','算力产业链股票'):
                    params=dict(searchstring=query,searchtype='stock')
                    self.assertEqual(validate_source_request(task,'ifind','smart_stock_picking',params),params)
                params=dict(reportname='p03291',functionpara=dict(date='20261016',
                    blockname=PROFILES['000016.SH']['blockname'],iv_type='allcontract'),outputpara=OUTPUT)
                self.assertEqual(validate_source_request(task,'ifind','data_pool',params),params)
            finally:agent.close_runtime()
