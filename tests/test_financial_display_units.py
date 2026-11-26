"""Readable registered monetary units, without rewriting frozen research."""
import copy
import hashlib
import json
import os
from pathlib import Path
import unittest

from researchhelper.contracts import FINANCIAL_AMOUNT_UNITS, fingerprint
from researchhelper.reports import READER_VERSION
from researchhelper.visual import (MONEY_UNIT_FACTORS, display_money_scale,
    statement_display_text, render)

ROOT = Path(__file__).resolve().parents[1]
from tests.financial_presentation_fixture import SOURCE as PRESENTATION_FIXTURE, load_content
SOURCE = Path(os.environ.get('RESEARCHHELPER_CASHFLOW_REPORT_INPUT', str(PRESENTATION_FIXTURE)))


class FinancialDisplayUnitTests(unittest.TestCase):
    def quantity(self, value, unit, metric='revenue'):
        text = str(value)+unit
        evidence = dict(id='explicit_amount', value=value, unit=unit,
                        calculation_context=dict(metric=metric))
        binding = dict(span=[0, len(text)], display=text, source_kind='evidence',
            ref=evidence['id'], unit=unit, path='value', canonical_value=str(value), projection='identity')
        record = dict(answer=text, numeric_integrity=dict(fields=dict(answer=dict(
            text_hash=fingerprint(text), bindings=[binding]))))
        return record, dict(evidence=[evidence], calculations=[])

    def test_every_registered_amount_unit_has_one_explicit_display_factor(self):
        self.assertEqual(set(MONEY_UNIT_FACTORS), set(FINANCIAL_AMOUNT_UNITS))
        for unit, factor in MONEY_UNIT_FACTORS.items():
            figure = dict(unit=unit, rows=[dict(value=100_000_000/factor)])
            before = copy.deepcopy(figure)
            shown = display_money_scale(figure)
            self.assertEqual(shown['unit'], '亿元')
            self.assertEqual(shown['source_unit'], unit)
            self.assertEqual(figure['rows'][0]['value']/shown['divisor'], 1)
            self.assertEqual(figure, before)

    def test_thousand_yuan_prose_links_only_change_reading_precision(self):
        record, content = self.quantity(276916580, '千元')
        before = copy.deepcopy((record, content))
        self.assertEqual(statement_display_text(record, 'answer', content), '2,769.17亿元')
        self.assertEqual((record, content), before)
        negative, values = self.quantity(-47030638, '千元', 'net_profit')
        self.assertEqual(statement_display_text(negative, 'answer', values), '-470.31亿元')

    def test_unknown_currency_unbound_money_and_price_observations_do_not_convert(self):
        for unit in ('万美元', '%', '点'):
            figure = dict(unit=unit, rows=[dict(value=100000000)])
            self.assertFalse(display_money_scale(figure)['monetary'])
        for kind in ('points', 'observations'):
            self.assertFalse(display_money_scale(dict(kind=kind, unit='千元', rows=[dict(value=100000000)]))['monetary'])
        record, content = self.quantity(123456, '千元', 'close')
        self.assertEqual(statement_display_text(record, 'answer', content), '123456千元')
        record['numeric_integrity']['fields']['answer']['text_hash'] = 'not_the_frozen_text'
        self.assertEqual(statement_display_text(record, 'answer', content), '123456千元')

    def test_real_completed_research_remains_frozen_when_presented_in_billions(self):
        original = SOURCE.read_bytes()
        content = load_content(SOURCE)
        before = copy.deepcopy(content)
        resources = Path(os.environ.get('RESEARCHHELPER_CASHFLOW_REPORT_RESOURCES', str(ROOT)))
        page = render(content, resources)
        self.assertEqual(content, before)
        self.assertEqual(SOURCE.read_bytes(), original)
        self.assertTrue(f"data-reader-version='{READER_VERSION}'" in page)
        self.assertIn('2,769.17亿元', page)
        self.assertIn('470.31亿元', page)
        self.assertIn('602.17亿元', page)
        self.assertIn('原值：276,916,580千元', page)
        self.assertIn('图示单位亿元', page)
        self.assertNotIn('<details', page)
        target = os.environ.get('RESEARCHHELPER_CASHFLOW_REPORT_EXPORT')
        if target:
            Path(target).write_text(page)
            self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).digest(), hashlib.sha256(original).digest())


if __name__ == '__main__': unittest.main()
