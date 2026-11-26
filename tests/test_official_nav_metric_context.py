"""Official per-share NAV labels must bind the selected numeric value."""
import unittest

from tests.legacy_replay_fixtures import ETF_NAV
from tests.test_listed_etf_unit_context import ListedETFUnitContextTests
from researchhelper.contracts import ContractError
from researchhelper.evidence import candidate_evidence, validate_calculation_context_basis, validate_official_nav_number


class OfficialNavMetricContextTests(unittest.TestCase):
    def setUp(self):
        ListedETFUnitContextTests.setUpClass()
        fixture = ListedETFUnitContextTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.task = fixture.task
        decoded, _ = fixture.checked_inputs()
        self.spec = decoded['evidence_specs'][1]
        self.source = next(item for item in self.task['source_records']
                           if item['id'] == self.spec['artifact_id'])
        self.spec.update(id='unit_nav_number_projection', calculation_context={'nav_kind':'official'})
        self.spec['calculation_context_basis'] = {'nav_kind':dict(kind='material',
            artifact_id=self.source['id'], snapshot_hash=self.source['hash'],
            part=self.spec['record_locator']['part'], quote=self.spec['text'])}

    def test_actual_quarterly_fund_share_nav_label_is_a_numeric_nav_not_plain_text_only(self):
        result = candidate_evidence(self.task, self.source, self.spec)
        self.assertEqual(result['value'], float(ETF_NAV))
        self.assertEqual(result['calculation_context']['nav_kind'], 'official')
        self.assertNotIn('share_class', result['calculation_context'])
        self.assertFalse(self.task['calculations'])

    def test_nearby_growth_percentage_cannot_borrow_the_official_nav_label(self):
        item = dict(self.spec, task_id=self.task['id'], research_version=self.task['version'],
                    raw_token='0.61', raw_value=0.61, value=0.61)
        with self.assertRaises(ContractError):
            validate_calculation_context_basis(item, self.task['source_records'])

    def test_registered_labels_accept_inline_and_single_value_rows_without_rewriting(self):
        for quote in (f'基金份额净值为{ETF_NAV}元', f'期末基金份额净值（元）\t{ETF_NAV}', f'单位净值：{ETF_NAV}元'):
            with self.subTest(quote=quote):
                self.assertTrue(validate_official_nav_number(self.spec, quote))

    def test_reference_cumulative_and_growth_labels_are_not_official_unit_nav(self):
        for quote in (f'累计单位净值为{ETF_NAV}元', f'累计基金份额净值为{ETF_NAV}元',
                      f'参考基金份额净值为{ETF_NAV}元', f'基金份额参考净值为{ETF_NAV}元',
                      f'预估单位净值为{ETF_NAV}元', f'IOPV {ETF_NAV}，单位净值为{ETF_NAV}元',
                      f'基金份额净值增长率为{ETF_NAV}%', f'基金资产净值为{ETF_NAV}元',
                      f'单位净值为{ETF_NAV}3元', f'单位净值为{ETF_NAV}%'):
            with self.subTest(quote=quote), self.assertRaises(ContractError):
                validate_official_nav_number(self.spec, quote)


if __name__ == '__main__':
    unittest.main()
