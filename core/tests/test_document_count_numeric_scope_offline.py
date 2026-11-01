"""Distinguish file counts from fund-share quantities, never waive returns."""

import unittest

from researchhelper.numeric_integrity import _quantities


class DocumentCountNumericScopeTests(unittest.TestCase):
    def test_actual_source_snapshot_wording_is_not_a_financial_quantity(self):
        self.assertEqual(_quantities('同一份 iFinD cmd_history_quotation 响应快照'), [])
        self.assertEqual(_quantities('一份原件、两份报告与三份接口响应'), [])

    def test_bare_shares_and_fund_units_still_require_numeric_binding(self):
        for text in ('买入100份。', '一份基金份额', '一份ETF净值为5元', '一份 ETF 的净值为5元'):
            with self.subTest(text=text):
                self.assertTrue(any(item['unit'] == '份' for item in _quantities(text)))

    def test_document_noun_cannot_hide_nearby_financial_numbers(self):
        values = _quantities('一份报告称收益为20%，买入100份基金花费500元。')
        self.assertEqual([item['unit'] for item in values], ['%', '份', '元'])
        self.assertEqual([str(item['value']) for item in values], ['20', '100', '500'])

    def test_financial_suffix_prevents_document_word_prefix_exemption(self):
        for text in ('100份报告基金', '100份公告证券', '100份文件股票', '100份响应快照基金'):
            with self.subTest(text=text):
                self.assertTrue(any(item['unit'] == '份' for item in _quantities(text)))


if __name__ == '__main__':
    unittest.main()
