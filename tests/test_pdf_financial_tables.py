"""Actual retained PDF geometry, legacy replay and financial rejection gates.

This is parser/validator acceptance, not a new live study or financial approval.
The original closed task and its report are never edited or reopened.
"""
import base64
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import extract_material, validate_calculation_context_basis
from researchhelper.financial_sources import validate_financial_metric_number, _pdf_metric_label
from researchhelper.material_parser import verified_material, MaterialParser
from researchhelper.pdf_material import EXTRACT_VERSION

ROOT = Path(__file__).resolve().parents[1]
RETAINED = ROOT/'result/acceptance/default-app-investment-zmljdjci/task.json'


class PdfFinancialTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        task = json.loads(RETAINED.read_text())
        cls.old = next(record['snapshot']['material'] for record in task['source_records']
                       if record['kind'] == 'filing')
        cls.payload = base64.b64decode(cls.old['original_base64'], validate=True)
        cls.material = extract_material(cls.old['name'], cls.payload)
        cls.material['original_base64'] = cls.old['original_base64']
        cls.by_id = {part['locator']: part for part in cls.material['parts']}
        cls.record = dict(id='fixture_pdf', kind='material', snapshot=cls.material)

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(RETAINED.read_bytes()).hexdigest() == cls.before

    def proposal(self, part, row, *, metric, token, header='第30页表2', unit_part='第30页'):
        header_quote = self.by_id[header]['text'].split('\n')[0]
        item = dict(artifact_id='fixture_pdf', text=row, raw_token=token, raw_unit='元', value=float(token.replace(',', '')),
            calculation_context=dict(metric=metric, period='2026年半年度'),
            record_locator=dict(part=part, unit_part=unit_part, financial_column=dict(version=2,
                header_part=header, header_quote=header_quote, row_quote=row, column_index=2)))
        return item

    def row(self, locator, term):
        return next(row for row in self.by_id[locator]['text'].split('\n') if term in row)

    def check(self, item):
        validate_financial_metric_number(item, self.record, dict(part=item['record_locator']['part'],
            quote=item['record_locator']['financial_column']['row_quote']))

    def test_every_legacy_plain_page_and_original_byte_identity_is_preserved(self):
        plain = [part for part in self.material['parts'] if 'pdf_table' not in part]
        self.assertEqual(plain, self.old['parts'])
        self.assertEqual(self.material['hash'], hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(self.material['extract_version'], EXTRACT_VERSION)

    def test_legacy_version_reproduces_the_original_without_new_tables(self):
        legacy = extract_material(self.old['name'], self.payload, extract_version='1')
        self.assertEqual(legacy['parts'], self.old['parts'])
        legacy['original_base64'] = self.old['original_base64']
        self.assertEqual(verified_material(legacy, self.payload)['parts'], self.old['parts'])

    def test_bounded_worker_returns_the_same_table_structure_and_is_reaped(self):
        parsed = MaterialParser().parse(self.old['name'], self.payload)
        self.assertEqual(parsed['status'], '已解析')
        self.assertEqual(parsed['parts'], self.material['parts'])
        self.assertTrue(parsed['parse_runtime']['parser_worker_reaped'])
        self.assertFalse(parsed['parse_runtime']['network_request_started'])

    def test_actual_income_row_keeps_the_note_column_and_both_periods(self):
        part = '第30页表2'; row = self.row(part, '其中：营业收入')
        self.assertEqual(row.split('\t')[1], '43')
        item = self.proposal(part, row, metric='revenue', token=row.split('\t')[2])
        self.check(item)

    def test_actual_net_profit_uses_the_explicit_continuation_header(self):
        part = '第31页表1'; row = self.row(part, '五、净利润')
        self.assertEqual(self.by_id[part]['pdf_table']['group'], self.by_id['第30页表2']['pdf_table']['group'])
        item = self.proposal(part, row, metric='net_profit', token=row.split('\t')[2])
        self.check(item)

    def test_actual_operating_cash_flow_has_its_own_statement_header(self):
        part = '第34页表1'; row = self.row(part, '经营活动产生的现金流量净')
        header = '第33页表2'
        item = self.proposal(part, row, metric='operating_cash_flow', token=row.split('\t')[2],
                             header=header, unit_part='第33页')
        self.check(item)

    def test_wrapped_cash_flow_label_passes_the_full_original_context_gate(self):
        part = '第34页表1'; row = self.row(part, '经营活动产生的现金流量净')
        item = self.proposal(part, row, metric='operating_cash_flow', token=row.split('\t')[2],
                             header='第33页表2', unit_part='第33页')
        record = dict(self.record, task_id='fixture_task', research_version=1,
                      snapshot_hash=fingerprint(self.material))
        item.update(task_id='fixture_task', research_version=1,
            calculation_context_basis={'metric': dict(kind='material', artifact_id='fixture_pdf',
                snapshot_hash=record['snapshot_hash'], part=part, quote=row),
                'period': dict(kind='material', artifact_id='fixture_pdf', snapshot_hash=record['snapshot_hash'],
                    part='第33页表2', quote=self.by_id['第33页表2']['text'].split('\n')[0])})
        validate_calculation_context_basis(item, [record])

    def test_a_header_from_the_parent_statement_cannot_bind_consolidated_profit(self):
        part = '第31页表1'; row = self.row(part, '五、净利润')
        item = self.proposal(part, row, metric='net_profit', token=row.split('\t')[2], header='第32页表2')
        with self.assertRaises(ContractError): self.check(item)

    def test_prior_period_and_wrong_numeric_column_are_rejected(self):
        part = '第30页表2'; row = self.row(part, '其中：营业收入')
        for index, token in ((3, row.split('\t')[3]), (2, row.split('\t')[3]), (1, '43')):
            item = self.proposal(part, row, metric='revenue', token=token)
            item['record_locator']['financial_column']['column_index'] = index
            with self.assertRaises(ContractError): self.check(item)

    def test_parent_adjusted_and_total_revenue_labels_are_not_simplified(self):
        for value, label in [('归属于母公司股东的净利润', '净利润'),
            ('扣除非经常性损益后的净利润', '净利润'), ('营业总收入', '营业收入'),
            ('一、经营活动产生的现金流量：', '经营活动产生的现金流量净额')]:
            self.assertFalse(_pdf_metric_label(value, label))

    def test_table_shape_cannot_be_asserted_on_unstructured_or_legacy_text(self):
        part = '第30页表2'; row = self.row(part, '其中：营业收入')
        item = self.proposal(part, row, metric='revenue', token=row.split('\t')[2])
        record = copy.deepcopy(self.record); record['snapshot']['extract_version'] = '1'
        with self.assertRaises(ContractError):
            validate_financial_metric_number(item, record, dict(part=part, quote=row))

    def test_changed_cell_geometry_fails_original_reproduction(self):
        changed = copy.deepcopy(self.material)
        table = next(part for part in changed['parts'] if 'pdf_table' in part)
        table['pdf_table']['column_bounds'][0] += 10
        with self.assertRaises(ContractError): verified_material(changed, self.payload)

    def test_unknown_extraction_version_is_not_silently_upgraded(self):
        with self.assertRaises(ContractError):
            extract_material(self.old['name'], self.payload, extract_version='pdf-unknown')

    def test_unread_table_header_is_rejected_even_when_the_amount_row_was_exposed(self):
        from researchhelper import source_reader
        part = '第30页表2'; row = self.row(part, '其中：营业收入')
        item = self.proposal(part, row, metric='revenue', token=row.split('\t')[2])
        item['artifact_id'] = 'fixture_pdf'
        item['record_locator']['financial_column']['header_part'] = '第33页表2'
        bundle = {'views': [dict(id='fixture_pdf', kind='material', parts=[dict(locator=part, text=row)])]}
        # Isolate the exposure gate; real original/geometry checks are covered
        # separately above. No source or financial approval is produced here.
        with patch.object(source_reader, 'validate_reading'):
            with self.assertRaisesRegex(ContractError, '不在本轮实际读取'):
                source_reader.validate_read_specs({}, [item], bundle)


if __name__ == '__main__':
    unittest.main()
