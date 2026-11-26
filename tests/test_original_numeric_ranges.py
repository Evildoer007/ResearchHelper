"""Interval endpoints stay in their original observation across delivery."""
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, DOCUMENT_CONTENT_INTENT
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import candidate_evidence
from researchhelper.numeric_integrity import literal_quantity_ranges


class OriginalNumericRangeTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix='quantity-ranges-', dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True,
                                   require_argument_support=True, draft_first=True)
        self.addCleanup(self.agent.close_runtime)
        guard = patch('socket.socket.connect', side_effect=AssertionError('Offline range replay'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_range_syntax_keeps_native_units_and_excludes_changes_and_table_columns(self):
        for text, wanted in (
            ('0.00%至 4.00%', ('0.00', '4.00', '%', '%')),
            ('1,000–1,200万元', ('1000', '1200', '万元', '万元')),
            ('−2%至\n+3%', ('-2', '3', '%', '%')),
            ('报告中列出约0.50％～6.00％', ('0.50', '6.00', '%', '%')),
            ('1亿元至2,000万元', ('1', '2000', '亿元', '万元')),
            ('介于从5%到8%之间', ('5', '8', '%', '%')),
        ):
            with self.subTest(text=text):
                span, = literal_quantity_ranges(text)
                self.assertEqual((str(span.first), str(span.last), span.first_unit, span.last_unit), wanted)
        for text in ('利润由5%升至8%', '利润从5%到8%', '从100万元至120万元',
                     '40,656,475  53,625,499  -24.18%', '46.02% 49.38% -1.78%',
                     '期间2026-01-01至2026-06-30', '上午9—10点', '3-5份公告'):
            with self.subTest(text=text):
                self.assertEqual(list(literal_quantity_ranges(text)), [])

    def test_report_preserves_one_link_for_the_full_interval_and_rejects_unverified_endpoints(self):
        task = self.agent.create('核对600030.SH短期借款利率及归母净利润', objects=['600030.SH'])
        source = ('短期借款利率区间为0.00%至 \n4.00%。\n\n'
                  '2026年上半年归母净利润同比增长5%至8%。\n\n'
                  '2026年上半年营业收入为36亿元。')
        original = self.agent.add_material(task['id'], '合成披露.txt', source.encode(), return_record=True)
        supported = '短期借款利率区间为0%—4%。'
        proposal = dict(title='借款与利润', conclusion='对照披露区间。', logics=[
            dict(title='区间核对', analysis=supported, source_refs=[original['id']]),
            dict(title='数字边界', analysis='2026年上半年归母净利润同比增长8%。'
                 '2026年上半年归母净利润同比增长5%至9%。2026年上半年营业收入为36亿元。',
                 source_refs=[original['id']])])
        done = self.agent.start(task['id'], dict(draft_report=proposal, data_requests=[]))
        self.assertTrue(done.get('delivery'), done.get('error'))
        first, second = done['draft_report']['logics']
        self.assertEqual(first['analysis'], supported)
        link, = first['quantity_links']['analysis']['links']
        self.assertEqual(link['original'], '0%—4%')
        part = source.split('\n\n')[link['document']['part_index']]
        self.assertIn('0.00%至 \n4.00%', part[link['document']['start']:link['document']['end']])
        self.assertNotIn('增长8%', second['analysis'])
        self.assertNotIn('5%至9%', second['analysis'])
        self.assertIn('营业收入为36亿元', second['analysis'])

    def test_original_endpoint_is_rejected_even_when_the_selection_omits_the_other_bound(self):
        task = self.agent.create('核对600030.SH公告里的费率', objects=['600030.SH'], intent=DOCUMENT_CONTENT_INTENT)
        original = self.agent.add_material(task['id'], '合成借款说明.txt',
            '披露借款利率0.00%至\n4.00%。另有合同的单点利率5.00%。'.encode(), return_record=True)
        task = self.agent.archive.get(task['id'])

        def number(raw):
            return dict(id='literal_rate', artifact_id=original['id'], object='600030.SH',
                record_locator=dict(part='段落1'), text=raw+'%', period='原件所载期间', nature='已实现事实',
                unit='%', raw_unit='%', raw_token=raw, value=float(raw), raw_value=float(raw), conversion_factor=1,
                question_ids=['q1'], usage_scope='document_content', published_at=None,
                publication=dict(at=None, basis_ref=None, precision='未知', timezone='未知',
                    certainty='未知', basis='首次公开时间未核验'))

        for endpoint in ('0.00', '4.00'):
            with self.subTest(endpoint=endpoint), self.assertRaisesRegex(ContractError, '完整区间摘录'):
                candidate_evidence(task, original, number(endpoint))
        self.assertEqual(candidate_evidence(task, original, number('5.00'))['value'], 5)
