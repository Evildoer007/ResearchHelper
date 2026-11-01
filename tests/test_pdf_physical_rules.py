"""Physical-rule parsing against retained original bytes; no new study.

This reviews isolated copies, never upgrades the closed task or approves its
financial inputs. Legacy parsing must reproduce its original representation.
"""
import base64
import copy
from types import SimpleNamespace
import unittest

from tests import ROOT
from tests.test_asset_data_acceptance_review import load_case
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import extract_material
from researchhelper.financial_sources import (financial_table_candidates,
    financial_context_cells, validate_financial_metric_number, _pdf_metric_label,
    financial_reporting_currency_choices, validate_financial_currency_basis)
from researchhelper.pdf_material import physical_rule_settings
from researchhelper.reasoning import extractable_source_views


class PdfPhysicalRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_task, _ = load_case('equity_financials')
        cls.before = fingerprint(cls.original_task)
        cls.task = copy.deepcopy(cls.original_task)
        cls.source = cls.task['source_records'][-1]
        cls.old = cls.source['snapshot']['material']
        cls.payload = base64.b64decode(cls.old['original_base64'], validate=True)
        cls.material = extract_material(cls.old['name'], cls.payload)
        cls.material['original_base64'] = cls.old['original_base64']
        cls.source['snapshot']['material'] = cls.material
        cls.source['hash'] = fingerprint(cls.source['snapshot'])
        cls.view = extractable_source_views(cls.task)[-1]
        cls.catalogue = financial_table_candidates(cls.task, cls.source, cls.view, maximum=64)

    @classmethod
    def tearDownClass(cls):
        assert fingerprint(load_case('equity_financials')[0]) == cls.before

    def test_backgrounds_do_not_create_phantom_rules(self):
        background = dict(width=150, height=17, stroke=False)
        vertical = dict(width=.24, height=17, stroke=False)
        horizontal = dict(width=160, height=.24, stroke=False)
        stroked = dict(width=150, height=17, stroke=True)
        page = SimpleNamespace(lines=[], rects=[background, vertical, horizontal, stroked])
        found = physical_rule_settings(page)
        self.assertEqual(found['explicit_vertical_lines'], [vertical, horizontal, stroked])
        self.assertEqual(found['vertical_strategy'], 'explicit')
        self.assertIsNone(physical_rule_settings(SimpleNamespace(lines=[], rects=[background])))

    def test_legacy_v3_representation_is_not_changed(self):
        legacy = extract_material(self.old['name'], self.payload, extract_version='pdf-tables-3')
        self.assertEqual(legacy['parts'], self.old['parts'])
        self.assertEqual(legacy['hash'], self.old['hash'])

    def test_new_parser_preserves_every_plain_page_and_original_byte_hash(self):
        self.assertEqual(self.material['extract_version'], 'pdf-tables-4')
        self.assertEqual(self.material['hash'], self.old['hash'])
        self.assertEqual([p for p in self.material['parts'] if 'pdf_table' not in p],
                         [p for p in self.old['parts'] if 'pdf_table' not in p])

    def test_financial_cells_have_actual_periods_and_integer_thousand_units(self):
        targets = {('revenue', '第77页表2'): 276916580,
                   ('net_profit', '第78页表1'): 47030638,
                   ('operating_cash_flow', '第81页表1'): 60216851}
        for (metric, part), amount in targets.items():
            cells = [c for c in self.catalogue['cells'] if c['metric'] == metric
                     and c['proposal_fields']['period'] == '2026年半年度'
                     and c['proposal_fields']['record_locator']['part'] == part]
            self.assertEqual(len(cells), 1)
            fields = cells[0]['proposal_fields']
            self.assertEqual(fields['raw_value'], amount)
            self.assertEqual(fields['raw_unit'], '千元')
            self.assertEqual(fields['record_locator']['financial_column']['column_index'], 1)
            self.assertFalse(self.catalogue['candidate_is_approval'])

    def test_wrong_amount_and_wrong_parent_header_remain_rejected(self):
        cell = next(c for c in self.catalogue['cells'] if c['metric'] == 'net_profit'
                    and c['proposal_fields']['record_locator']['part'] == '第78页表1')
        original = copy.deepcopy(cell['proposal_fields'])
        original['calculation_context'] = dict(metric='net_profit', period=original['period'])
        anchor = dict(part='第78页表1', quote=original['text'])
        validate_financial_metric_number(original, self.source, anchor)
        wrong = copy.deepcopy(original); wrong['raw_token'] = '43284002'
        with self.assertRaises(ContractError):
            validate_financial_metric_number(wrong, self.source, anchor)
        wrong = copy.deepcopy(original)
        heading = next(p for p in self.material['parts'] if p['locator'] == '第79页表2')
        wrong['record_locator']['financial_column'].update(
            header_part=heading['locator'], header_quote=heading['text'].splitlines()[0])
        with self.assertRaises(ContractError):
            validate_financial_metric_number(wrong, self.source, anchor)

    def test_reporting_currency_is_a_literal_wrapped_original_not_functional_currency(self):
        choices=financial_reporting_currency_choices(self.source,self.view)
        self.assertTrue(choices)
        self.assertTrue(any('人民\n币' in row['quote'] for row in choices))
        cells=financial_context_cells(self.task,self.source,self.view)
        self.assertTrue(cells)
        self.assertEqual({row['context']['currency'] for row in cells},{'CNY'})
        part=next(p for p in self.material['parts'] if p['locator']=='第95页')
        original=part['text']
        for row in cells:
            self.assertEqual(row['bases']['currency']['part'],'第95页')
            self.assertIn(row['bases']['currency']['quote'],original)
        view=copy.deepcopy(self.view)
        view['parts']=[p for p in view['parts'] if p['locator']!='第95页']
        self.assertEqual(financial_context_cells(self.task,self.source,view),[])

    def test_functional_currency_only_and_wrong_source_page_cannot_approve_currency(self):
        item=dict(calculation_context=dict(currency='CNY'),record_locator=dict(unit_part='第77页'))
        for quote in ('本公司及境内子公司以人民币为记账本位币。','人民币千元','币种：人民币'):
            with self.assertRaises(ContractError):
                validate_financial_currency_basis(item,self.source,dict(part='第95页',quote=quote))
        choice=financial_reporting_currency_choices(self.source,self.view)[0]
        validate_financial_currency_basis(item,self.source,choice)

    def test_literal_profit_annotation_does_not_admit_parent_or_adjusted_profit(self):
        self.assertTrue(_pdf_metric_label('五、净利润（净亏损以“—”号填列）', '净利润'))
        for text in ('归属于母公司股东的净利润', '扣除非经常性损益后的净利润', '少数股东净利润'):
            self.assertFalse(_pdf_metric_label(text, '净利润'))
