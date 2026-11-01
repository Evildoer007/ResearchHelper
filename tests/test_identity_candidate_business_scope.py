"""Version2 may retrieve actual candidate codes, never approve identity/facts."""
import unittest

from tests import test_multimarket_identity_candidates as fixtures
from researchhelper.contracts import ContractError
from researchhelper.evidence import (available_security_codes,identity_candidates,identity_resolutions,
    validate_source_request,data_request_contract,identity_period_basis_contract)
from researchhelper.planning import input_fingerprint


class CandidateBusinessScopeTests(unittest.TestCase):
    retain_candidates = fixtures.MultimarketIdentityCandidateTests.retain_candidates

    def setUp(self):
        fixtures.MultimarketIdentityCandidateTests.setUp(self)
        self.task['identity_candidate_lookup_version']=2
        self.task['input_hash']=input_fingerprint(self.task)

    def test_actual_full_codes_are_query_scope_not_unique_identity(self):
        self.retain_candidates()
        self.assertEqual(available_security_codes(self.task),['0700.HK'])
        self.assertEqual(identity_resolutions(self.task)[0]['status'],'候选待确定')
        self.assertFalse(identity_candidates(self.task)[0]['identity_approved'])
        self.assertEqual(identity_period_basis_contract(self.task)['admissible_bases'],[])

    def test_original_code_is_not_padded_or_reassigned(self):
        self.retain_candidates()
        params=dict(codes='0700.HK',indicators='close',startdate='2026-10-12',enddate='2026-10-16',
            functionpara=dict(Currency='RMB',CPS='1',Fill='Omit'))
        self.assertEqual(validate_source_request(self.task,'ifind','cmd_history_quotation',params),params)
        for code in ('00700.HK','TCTZF.PQ','300750.SZ'):
            with self.assertRaises(ContractError):validate_source_request(self.task,'ifind','cmd_history_quotation',dict(params,codes=code))

    def test_all_supported_markets_remain_and_none_is_auto_selected(self):
        self.retain_candidates('宁德时代','300750.SZ,3750.HK,CYATY.PQ')
        self.assertEqual(available_security_codes(self.task),['300750.SZ','3750.HK'])
        self.assertEqual(self.task['objects'],[])
        self.assertFalse(identity_candidates(self.task)[0]['market_selected'])
        self.assertEqual(self.task['ledger']['used']['data_requests'],0)

    def test_no_second_numeric_conversion_is_advertised_for_returned_code(self):
        self.retain_candidates()
        tool=next(row for row in data_request_contract(self.task)['tools'] if row['operation']=='get_thscode')
        self.assertEqual(tool['query_value_options'],[])
