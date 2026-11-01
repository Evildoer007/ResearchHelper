"""Official per-share NAV labels must bind the selected numeric value."""
import copy
import json
import unittest

from tests import ROOT
from researchhelper.contracts import ContractError
from researchhelper.evidence import candidate_evidence, validate_calculation_context_basis, validate_official_nav_number


RETAINED = ROOT / 'result/acceptance/one-sentence-research-8bhoacvp/task.json'


class OfficialNavMetricContextTests(unittest.TestCase):
    def setUp(self):
        self.original = RETAINED.read_bytes()
        self.task = json.loads(self.original)
        self.spec = copy.deepcopy(next(item for item in self.task['host_result']['evidence_specs']
            if item['id'] == 'ev_nav_statement_20260630'))
        self.source = next(item for item in self.task['source_records']
                           if item['id'] == self.spec['artifact_id'])
        self.spec.update(id='unit_nav_number_projection', raw_token='2.3272', raw_value=2.3272,
                         raw_unit='元', value=2.3272, unit='元', conversion_factor=1,
                         calculation_context={'nav_kind':'official'})
        self.spec['calculation_context_basis'] = {'nav_kind':dict(kind='material',
            artifact_id=self.source['id'], snapshot_hash=self.source['hash'],
            part=self.spec['record_locator']['part'], quote=self.spec['text'])}

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(), self.original)

    def test_actual_quarterly_fund_share_nav_label_is_a_numeric_nav_not_plain_text_only(self):
        result = candidate_evidence(self.task, self.source, self.spec)
        self.assertEqual(result['value'], 2.3272)
        self.assertEqual(result['calculation_context']['nav_kind'], 'official')
        self.assertNotIn('share_class', result['calculation_context'])
        self.assertFalse(self.task['calculations'])

    def test_nearby_growth_percentage_cannot_borrow_the_official_nav_label(self):
        item = dict(self.spec, task_id=self.task['id'], research_version=self.task['version'],
                    raw_token='75.82', raw_value=75.82, value=75.82)
        with self.assertRaises(ContractError):
            validate_calculation_context_basis(item, self.task['source_records'])

    def test_registered_labels_accept_inline_and_single_value_rows_without_rewriting(self):
        for quote in ('基金份额净值为2.3272元', '期末基金份额净值（元）\t2.3272', '单位净值：2.3272元'):
            with self.subTest(quote=quote):
                self.assertTrue(validate_official_nav_number(self.spec, quote))

    def test_reference_cumulative_and_growth_labels_are_not_official_unit_nav(self):
        for quote in ('累计单位净值为2.3272元', '累计基金份额净值为2.3272元',
                      '参考基金份额净值为2.3272元', '基金份额参考净值为2.3272元',
                      '预估单位净值为2.3272元', 'IOPV 2.3272，单位净值为2.3272元',
                      '基金份额净值增长率为2.3272%', '基金资产净值为2.3272元',
                      '单位净值为2.32723元', '单位净值为2.3272%'):
            with self.subTest(quote=quote), self.assertRaises(ContractError):
                validate_official_nav_number(self.spec, quote)


if __name__ == '__main__':
    unittest.main()
