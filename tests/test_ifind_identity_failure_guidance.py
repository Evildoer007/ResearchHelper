"""Official error-code meanings, not vendor free-text heuristics or retries."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector,identity_query_parameters
from researchhelper.contracts import ContractError


class IdentityFailureGuidanceTests(unittest.TestCase):
    def test_multiple_identity_error_is_not_described_as_permission_or_no_data(self):
        with tempfile.TemporaryDirectory() as directory:
            agent=ResearchAgent(ResearchArchive(Path(directory)),ROOT,offline=True)
            task=agent.create('查询宁德时代',research_mode='current')
            collector=EvidenceCollector(offline=False)
            with patch.object(collector,'_http_json',return_value=dict(errorcode=-6102,errmsg='PRIVATE_UNTRUSTED_PAYLOAD')) as request:
                with self.assertRaises(ContractError) as caught:
                    collector.external(task,'ifind','get_thscode',identity_query_parameters('secname','宁德时代'),dict(access_token='FAKE_ACCESS'))
            self.assertEqual(request.call_count,1)
            self.assertEqual(task['ledger']['dispatches'][-1]['supplier_rejection_category'],'ambiguous_identity')
            self.assertIn('多个证券',str(caught.exception));self.assertNotIn('PRIVATE',str(task)+str(caught.exception))
            agent.close_runtime()

    def test_wrong_parameters_preserve_exact_failed_request(self):
        with tempfile.TemporaryDirectory() as directory:
            agent=ResearchAgent(ResearchArchive(Path(directory)),ROOT,offline=True)
            task=agent.create('查询00700.HK',objects=['00700.HK'],research_mode='current')
            collector=EvidenceCollector(offline=False)
            params=dict(codes='00700.HK',indicators='close',startdate='2026-10-12',enddate='2026-10-16',
                functionpara=dict(Currency='RMB',CPS='1',Fill='Omit'))
            with patch.object(collector,'_http_json',return_value=dict(errorcode=-4210,errmsg='PRIVATE_UNTRUSTED_PAYLOAD')) as request:
                with self.assertRaises(ContractError) as caught:collector.external(task,'ifind','cmd_history_quotation',params,dict(access_token='FAKE_ACCESS'))
            self.assertEqual(request.call_count,1)
            self.assertEqual(task['ledger']['dispatches'][-1]['supplier_rejection_category'],'invalid_parameters')
            self.assertIn('参数有误',str(caught.exception));self.assertNotIn('PRIVATE',str(task)+str(caught.exception))
            self.assertEqual(params['codes'],'00700.HK');agent.close_runtime()
