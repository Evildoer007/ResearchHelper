"""A raw batch does not close the existing, scoped disclosure route."""
import hashlib
import json
import unittest

from tests import ROOT
from researchhelper.evidence import data_request_contract
from researchhelper.output_schema import _planned_request_schemas, _matches
from researchhelper.reasoning import FINANCIAL_BATCH_FALLBACK_RULE

FIXTURE = ROOT / 'result/acceptance/one-sentence-research-tm5y1fad/task.json'


class BatchFinancialFallbackTests(unittest.TestCase):
    def setUp(self):
        raw = FIXTURE.read_bytes(); self.original = hashlib.sha256(raw).hexdigest()
        self.task = json.loads(raw)
        self.contract = data_request_contract(self.task)
        self.schema = dict(anyOf=_planned_request_schemas(self.task, self.contract))

    def tearDown(self):
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), self.original)

    def request(self, code='600030.SH'):
        tool = next(tool for tool in self.contract['tools'] if tool['operation'] == 'report_query')
        return dict(id='selected_disclosure', source='ifind', operation='report_query', question_ids=['q1'],
            parameters=dict(codes=code, beginrDate='2026-07-01', endrDate='2026-10-21',
                functionpara={'reportType':'901'}, **tool['fixed_parameters']),
            selection_reason='核对本次显式所选公司原件，不外推其他公司', catalogue_alternative_ref=None)

    def test_returned_industry_codes_still_offer_actual_disclosure_lookup(self):
        tool = next(tool for tool in self.contract['tools'] if tool['operation'] == 'report_query')
        offered = {code for profile in tool['filing_filter_profiles'] for code in profile['available_codes']}
        self.assertEqual(len(offered), 48)
        self.assertIn('600030.SH', offered)
        self.assertTrue(_matches(self.request(), self.schema))

    def test_unknown_code_does_not_gain_disclosure_access_through_batch_fallback(self):
        self.assertFalse(_matches(self.request('999999.SH'), self.schema))

    def test_fallback_distinguishes_untried_catalogue_from_actual_absence(self):
        self.assertIn('不能据此断言公告目录为空', FINANCIAL_BATCH_FALLBACK_RULE)
        self.assertIn('不把下载完整行业全部PDF作为开始分析的前提', FINANCIAL_BATCH_FALLBACK_RULE)
        self.assertIn('不把少数公司作为全行业代表', FINANCIAL_BATCH_FALLBACK_RULE)


if __name__ == '__main__': unittest.main()
