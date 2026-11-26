"""Diagnostic requests must be authorized by their actual task question."""
from pathlib import Path
import tempfile
import unittest
from tests import ROOT
from tests.accept_requested_asset_data_matrix import EXPLICIT_QUESTION
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import validate_source_request
from researchhelper.contracts import ContractError

class RequestedAssetDiagnosticInputTests(unittest.TestCase):
    def test_each_topic_is_explicit_not_inferred_from_a_generic_industry_word(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent=ResearchAgent(ResearchArchive(Path(temporary)),ROOT,offline=True,require_argument_support=True)
            try:
                task=agent.create(EXPLICIT_QUESTION,entry='混合',research_mode='current')
                for topic in ('证券行业股票','算力产业链股票'):
                    params=dict(searchstring=topic,searchtype='stock')
                    self.assertEqual(validate_source_request(task,'ifind','smart_stock_picking',params),params)
                generic=agent.create('仅检查行业和产业链，不指定主题',entry='混合',research_mode='current')
                with self.assertRaises(ContractError):
                    validate_source_request(generic,'ifind','smart_stock_picking',dict(searchstring='证券行业股票',searchtype='stock'))
            finally:
                agent.close_runtime()
