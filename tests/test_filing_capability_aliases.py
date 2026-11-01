"""Private cache aliases preserve fields, expiry and task ownership."""
import copy
import time
import unittest
from unittest.mock import Mock,patch
from researchhelper.contracts import ContractError,budget
from researchhelper.evidence import EvidenceCollector


class FilingCapabilityAliasTests(unittest.TestCase):
    def setUp(self):
        self.collector=EvidenceCollector(offline=False)
        self.rows=[{'source_id':s,'catalog_id':'old_catalog','catalog_ref':{'original_fields':{'seq':s,'title':'原文'+s}},
            'original_fetch':'ifind_temporary_resource'} for s in ('a','b')]
        self.catalog={'id':'old_catalog','parameters':{'codes':'510300.SH'},'catalog_entries':copy.deepcopy(self.rows)}
        self.task={'id':'owned_task','version':1,'source_records':[self.catalog],'ledger':budget()}
        self.fresh={'id':'fresh_catalog','catalog_entries':copy.deepcopy(self.rows)}
        self.collector.external=Mock(side_effect=self.refresh)
        guard=patch('socket.socket.connect',side_effect=AssertionError('offline capability test'))
        guard.start();self.addCleanup(guard.stop)

    def refresh(self,*args):
        for row in self.fresh['catalog_entries']:
            self.collector._ifind_original_cache[(self.task['id'],self.fresh['id'],row['source_id'])]=(row['source_id']+'-private-capability',time.monotonic()+60)
        return copy.deepcopy(self.fresh)

    def fetch(self,row):
        return self.collector._ifind_original_url(self.task,row,{'access_token':'OFFLINE_TEST_ONLY'})

    def test_two_different_originals_share_one_refresh_not_one_download(self):
        self.assertEqual(self.fetch(self.rows[0]),'a-private-capability')
        self.assertEqual(self.fetch(self.rows[1]),'b-private-capability')
        self.assertEqual(self.collector.external.call_count,1)
        self.assertEqual(len(self.task['source_records']),2)

    def test_no_private_capability_serialized_into_retained_catalog(self):
        self.fetch(self.rows[0])
        self.assertNotIn('private-capability',str(self.task))
        self.assertEqual(self.catalog['catalog_entries'],self.rows)

    def test_changed_selected_metadata_is_not_substituted(self):
        self.fresh['catalog_entries'][0]['catalog_ref']['original_fields']['title']='改动的原文'
        with self.assertRaises(ContractError):self.fetch(self.rows[0])

    def test_changed_unselected_row_is_not_aliased(self):
        self.fresh['catalog_entries'][1]['catalog_ref']['original_fields']['title']='不同版本'
        self.fetch(self.rows[0])
        self.assertNotIn((self.task['id'],'old_catalog','b'),self.collector._ifind_original_cache)

    def test_expired_capability_does_not_skip_budgeted_refresh(self):
        self.fetch(self.rows[0])
        self.collector._ifind_original_cache[(self.task['id'],'old_catalog','b')]=('old',time.monotonic()-1)
        self.assertEqual(self.fetch(self.rows[1]),'b-private-capability')
        self.assertEqual(self.collector.external.call_count,2)

    def test_other_task_cache_cannot_supply_original(self):
        self.collector._ifind_original_cache[('foreign','old_catalog','a')]=('foreign-url',time.monotonic()+60)
        self.assertEqual(self.fetch(self.rows[0]),'a-private-capability')
        self.assertEqual(self.collector.external.call_count,1)

    def test_insufficient_budget_never_refreshes(self):
        self.task['ledger']['used']['data_requests']=self.task['ledger']['limits']['data_requests']
        with self.assertRaises(ContractError):self.fetch(self.rows[0])
        self.collector.external.assert_not_called()


if __name__=='__main__':unittest.main()
