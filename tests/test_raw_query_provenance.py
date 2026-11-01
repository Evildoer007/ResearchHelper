"""Query receipts are available without granting raw fields financial approval."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (snapshot_record, _record_current_acquisition,
    current_query_reference, current_snapshot_reference)
from researchhelper.provenance import query_candidates, query_projection
from researchhelper.reasoning import evidence_contract
from researchhelper.market_series import SERIES_FAMILIES

ROOT=Path(__file__).resolve().parents[1]


class RawQueryProvenanceTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='raw-query-')
        self.addCleanup(folder.cleanup)
        agent=ResearchAgent(ResearchArchive(Path(folder.name)/'store'),ROOT)
        self.task=agent.create('核对600519.SH实际估值查询的来源记录',objects=['600519.SH'],research_mode='current')
        params=dict(codes='600519.SH',startdate='2026-09-30',enddate='2026-09-30',
            functionpara={'Fill':'Blank'},indipara=[dict(indicator='ths_pe_ttm_stock',
                indiparams=copy.deepcopy(SERIES_FAMILIES['个股']['ths_pe_ttm_stock']))])
        payload=dict(errorcode=0,tables=[dict(thscode='600519.SH',time=['2026-09-30'],
            table={'ths_pe_ttm_stock':[19.0]})])
        self.record=snapshot_record(self.task,'ifind','date_sequence',params,payload)
        self.task['source_records'].append(self.record)
        self.event=dict(kind='data_requests',batch=self.task['batch'],status='成功',
            fingerprint=self.record['request_hash'],transport_isolated=True,
            network_request_started=True,network_worker_reaped=True)
        self.task['ledger']['dispatches'].append(self.event)
        guard=patch('socket.socket.connect',side_effect=AssertionError('No network'))
        guard.start();self.addCleanup(guard.stop)

    def mint(self):
        _record_current_acquisition(self.task,self.record,self.event)

    def test_actual_registered_raw_request_can_have_query_receipt(self):
        self.mint()
        projection=query_projection(self.task,self.record)
        self.assertEqual(projection['operation'],'date_sequence')
        self.assertEqual(projection['parameters'],self.record['parameters'])
        self.assertEqual(projection['snapshot_hash'],fingerprint(self.record['snapshot']))
        self.assertEqual(len(query_candidates(self.task)),1)

    def test_query_receipt_cannot_approve_numeric_availability_or_cells(self):
        self.mint()
        self.assertIsNotNone(current_query_reference(self.task,self.record))
        self.assertIsNone(current_snapshot_reference(self.task,self.record))
        source=evidence_contract(self.task)['sources'][0]
        self.assertFalse(source['numeric_evidence_registered'])
        self.assertEqual(source['api_cells']['cells'],[])
        self.assertFalse(self.task['evidence'])

    def test_imported_or_unreaped_request_cannot_mint_provenance(self):
        self.assertFalse(query_candidates(self.task))
        for field in ('transport_isolated','network_request_started','network_worker_reaped'):
            self.event[field]=False;self.mint()
            self.assertNotIn('acquisition',self.record)
            self.event[field]=True

    def test_changed_response_and_request_invalidate_lineage(self):
        self.mint()
        for field in ('hash','request_hash'):
            original=self.record[field];self.record[field]='changed'
            with self.assertRaises(ContractError):query_projection(self.task,self.record)
            self.record[field]=original

    def test_historical_and_other_version_cannot_reuse_current_receipt(self):
        self.mint()
        self.task['research_mode']='historical'
        self.assertFalse(query_candidates(self.task))
        self.task['research_mode']='current';self.record['research_version']+=1
        self.assertFalse(query_candidates(self.task))

    def test_cancelled_or_unknown_operation_does_not_gain_receipt(self):
        self.event['status']='已取消';self.mint()
        self.assertNotIn('acquisition',self.record)
        self.event['status']='成功';self.record['operation']='unknown'
        self.mint();self.assertNotIn('acquisition',self.record)

    def test_provenance_projection_never_changes_original_record(self):
        self.mint();before=fingerprint(self.task)
        query_projection(self.task,self.record)
        self.assertEqual(before,fingerprint(self.task))

    def test_forged_dispatch_index_rejected(self):
        self.mint();self.record['acquisition']['dispatch_index']=True
        self.assertFalse(query_candidates(self.task))

    def test_retained_original_receipt_can_supply_metadata_not_financial_approval(self):
        try:
            from tests import test_current_original_availability as fixtures
        except ModuleNotFoundError:
            import test_current_original_availability as fixtures
        fixture=fixtures.CurrentOriginalAvailabilityTests()
        fixture.setUp()
        try:
            candidates=query_candidates(fixture.task)
            original=next(item for item in candidates if item['record']['operation']=='filing_original')
            self.assertEqual(original['record']['usage_scope'],'query_provenance')
            self.assertEqual(original['record']['snapshot_hash'],fixture.record['hash'])
            self.assertFalse(fixture.task['evidence'])
        finally:
            fixture.tearDown();fixture.doCleanups()


if __name__=='__main__':unittest.main()
