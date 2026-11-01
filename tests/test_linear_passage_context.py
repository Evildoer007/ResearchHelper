"""Page context must be linear in document size, without changing its meaning."""
import copy
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.financial_sources import passage_contexts
from researchhelper import financial_sources


class LinearPassageContextTests(unittest.TestCase):
    def source(self):
        parts = [dict(locator='第1页', text='目录\n十八、合并财务报表项目注释 …… 2'),
                 dict(locator='第2页', text='十八、合并财务报表项目注释\n收入说明'),
                 dict(locator='第3页', text='十九、将重分类\n保持原表内容'),
                 dict(locator='第3页表1', text='十九、 将重分类\n保持原表内容', pdf_table=dict(page=3)),
                 dict(locator='第4页', text='二十、母公司财务报表项目注释'),
                 dict(locator='第5页', text='二十一、补充资料')]
        snapshot = dict(material=dict(parts=parts))
        return dict(id='local_financial', kind='filing', task_id='local', research_version=1,
                    snapshot=snapshot, hash=fingerprint(snapshot))

    def test_page_table_order_versions_and_literal_text_are_preserved(self):
        source = self.source()
        order = ['第5页', '第3页表1', '第2页', '第4页', '第1页', '第3页表1']
        for version in (1, 2):
            values = passage_contexts(source, order, version=version, include_text=True)
            self.assertEqual([value['part'] for value in values], order)
            self.assertEqual([value['accounting_scope_hint'] for value in values],
                ['unspecified', 'unspecified' if version == 1 else 'consolidated',
                 'consolidated', 'parent', 'unspecified',
                 'unspecified' if version == 1 else 'consolidated'])
            self.assertEqual(values[1]['page_text'], '十九、将重分类\n保持原表内容')
            self.assertEqual(values[1]['physical_part'], '第3页')
            self.assertTrue(all(value['financial_approval'] is False for value in values))
            self.assertEqual(values[1], values[-1])
            values[1]['chapter']['quote'] = 'changed'
            self.assertNotEqual(values[1]['chapter'], values[-1]['chapter'])

    def test_table_normalization_is_not_repeated_for_every_selected_part(self):
        source = self.source()
        locators = [part['locator'] for part in source['snapshot']['material']['parts']] * 50
        original_sub = financial_sources.re.sub
        with patch.object(financial_sources.re, 'sub', wraps=original_sub) as substitutions:
            values = passage_contexts(source, locators, version=2)
        self.assertEqual(len(values), len(locators))
        self.assertLessEqual(substitutions.call_count, 8)

    def test_missing_or_duplicate_locator_never_borrows_another_page(self):
        source = self.source()
        with self.assertRaises(ContractError):
            passage_contexts(source, ['第100页'], version=2)
        duplicate = copy.deepcopy(source)
        duplicate['snapshot']['material']['parts'].append(copy.deepcopy(
            duplicate['snapshot']['material']['parts'][1]))
        duplicate['hash'] = fingerprint(duplicate['snapshot'])
        with self.assertRaises(ContractError):
            passage_contexts(duplicate, ['第2页'], version=2)

    def test_physical_page_is_required_and_mutation_invalidates_the_hash(self):
        source = self.source()
        source['snapshot']['material']['parts'] = [part for part in source['snapshot']['material']['parts']
                                                   if part['locator'] != '第3页']
        source['hash'] = fingerprint(source['snapshot'])
        with self.assertRaises(ContractError):
            passage_contexts(source, ['第3页表1'], version=2)
        source['snapshot']['material']['parts'][0]['text'] += 'changed'
        with self.assertRaises(ContractError):
            passage_contexts(source, ['第1页'], version=2)

    def test_one_snapshot_check_per_batch_and_no_scope_for_non_documents(self):
        source = self.source()
        with patch.object(financial_sources, 'fingerprint', wraps=fingerprint) as checked:
            passage_contexts(source, ['第2页', '第4页'], version=2)
        self.assertEqual(checked.call_count, 1)
        self.assertEqual(passage_contexts(dict(kind='api'), ['one', 'two']), [None, None])


if __name__ == '__main__':
    unittest.main()
