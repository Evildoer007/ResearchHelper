"""Explicit endpoint comparisons through ordinary offline research and reports."""

from copy import deepcopy
from datetime import date
from tempfile import TemporaryDirectory
import unittest

from lxml import html

from tests import ROOT
from tests.market_observation_fixtures import ETF_CLOSES, INDEX_OHLC, append_simulated_ifind_response, read_captures
from researchhelper.archive import ResearchArchive
from researchhelper.draft_sources import stated_window_requests
from researchhelper.draft_workflow import direct_readout
from researchhelper.engine import ResearchAgent
from researchhelper.market_calendar import (
    QUOTE_ENDPOINT_POLICY, close_return_calendar_requests, stated_close_pairs, stated_close_returns,
)
from researchhelper.planning import input_fingerprint
from researchhelper.report_designer import render_html

SUBJECT = '沪深300ETF510300.SH在2026年10月15日和2026年10月22日'
PAIR = f'比较{SUBJECT}的收盘价，列出两日收盘价和涨跌幅。'
EARLY, LATE = '2026-10-15', '2026-10-22'


class StatedClosePairTests(unittest.TestCase):
    def setUp(self):
        work = TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.agent = ResearchAgent(ResearchArchive(work.name), ROOT, offline=True, draft_first=True,
                                   require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.capture = read_captures(ETF_CLOSES.read_bytes(), grouped=False)

    def task(self, question):
        return self.agent.create(question, entry='ETF', research_mode='current')

    def replay(self, question, *, policy=None):
        task = self.task('本轮不联网。' + question)
        if policy is not None:
            task[QUOTE_ENDPOINT_POLICY] = policy
            task['input_hash'] = input_fingerprint(task)
        record = append_simulated_ifind_response(task, 'cmd_history_quotation',
            self.capture['parameters'], self.capture['snapshot'])
        record.update(question_ids=['q1'], data_need_ids=['retained_closes'])
        self.agent.archive.save(task)
        final = self.agent.start(task['id'])
        self.assertTrue(final.get('delivery'))
        self.assertEqual(final['source_records'], task['source_records'])
        for kind in ('data_requests', 'model_calls'):
            self.assertEqual(final['ledger']['used'][kind], task['ledger']['used'][kind])
        return final

    def test_interval_requests_use_the_two_literal_days_without_predecessors(self):
        for text in (PAIR, f'列出{SUBJECT}的收盘价，并计算区间涨跌幅。',
                     f'比较{SUBJECT}之间的收盘涨跌幅。',
                     '请比较510300.SH在2026年10月22日与15日的收盘价及区间涨跌幅。'):
            with self.subTest(question=text):
                task = self.task(text)
                [(start, end, question)] = stated_close_pairs(task)['510300.SH']
                self.assertEqual((start, end, question), (date.fromisoformat(EARLY), date.fromisoformat(LATE), 'q1'))
                self.assertEqual(direct_readout(task)[0], 'market_day')
                self.assertFalse(close_return_calendar_requests(task))
                self.assertFalse(stated_close_returns(task))
                requests = stated_window_requests(task)
                self.assertEqual([(row['operation'], row['parameters']['startdate'], row['parameters']['enddate'])
                                  for row in requests],
                                 [('cmd_history_quotation', EARLY, EARLY), ('cmd_history_quotation', LATE, LATE)])

    def test_adjacent_points_share_a_request_and_do_not_refetch_the_retained_endpoint(self):
        days = (EARLY, '2026-10-16')
        task = self.task(f'比较510300.SH在{days[0]}和{days[1]}的收盘价及区间涨跌幅。')

        def windows():
            return [(row['parameters']['startdate'], row['parameters']['enddate'])
                    for row in stated_window_requests(task)]

        self.assertEqual(windows(), [days])
        # Project the fixture onto the single day a previous run had retrieved.
        table = next(row for row in self.capture['snapshot']['tables'] if row['thscode'] == '510300.SH')
        close = table['table']['close'][table['time'].index(days[0])]
        parameters = dict(deepcopy(self.capture['parameters']), startdate=days[0], enddate=days[0])
        record = append_simulated_ifind_response(task, 'cmd_history_quotation', parameters,
            dict(errorcode=0, tables=[dict(thscode='510300.SH', time=[days[0]], table={'close': [close]})]))
        record.update(question_ids=['q1'], data_need_ids=['retained_first_close'])
        self.assertEqual(windows(), [(days[1], days[1])])

    def test_report_cites_original_endpoints_and_the_recomputed_interval(self):
        final = self.replay(PAIR)
        report = final['draft_report']
        self.assertEqual(report['title'], '510300.SH收盘价与区间涨跌幅对照')
        self.assertEqual([(row['period'], row['raw_value']) for row in report['facts']], [(EARLY, 4.242), (LATE, 4.107)])
        [calculation] = report['calculations']
        self.assertEqual((calculation['window_start'], calculation['window_end']), (EARLY, LATE))
        self.assertAlmostEqual(calculation['value'], (4.107 / 4.242 - 1) * 100)
        self.assertEqual(calculation['data_refs'], [row['id'] for row in report['facts']])
        content = self.agent.export(final['id'])['content']
        for profile in ('standard', 'detailed'):
            with self.subTest(profile=profile):
                paper = html.document_fromstring(render_html(content, ROOT, profile=profile))
                summary = paper.get_element_by_id('summary')
                self.assertIn('下降3.18%', summary.text_content())
                self.assertNotIn('上一交易日', summary.text_content())
                self.assertNotIn('研究判断', summary.text_content())
                links = summary.xpath('.//a[@class="quantity-link"]')
                self.assertEqual([link.text_content() for link in links], ['4.242', '4.107', '3.18%'])
                comparison = paper.get_element_by_id(links[-1].get('href')[1:])
                self.assertEqual(len(comparison.xpath('.//tr[@data-observation-id]')), 2)
                self.assertIn('iFinD', comparison.text_content())

    def test_dated_index_report_adopts_only_the_requested_close_cells(self):
        question = '本轮不联网。核对000016.SH和000300.SH在2026年10月30日的收盘点位。'
        task = self.agent.create(question, entry='指数', research_mode='current')
        capture = read_captures(INDEX_OHLC.read_bytes(), grouped=False)
        record = append_simulated_ifind_response(task, 'cmd_history_quotation',
            capture['parameters'], capture['snapshot'])
        record.update(question_ids=['q1'], data_need_ids=['retained_index_ohlc'])
        self.agent.archive.save(task)
        final = self.agent.start(task['id'])
        self.assertTrue(final.get('delivery'))
        self.assertEqual(final['source_records'][0]['snapshot'], capture['snapshot'])
        self.assertEqual(sorted((row['code'], row['field'], row['period'], row['raw_value'])
                                for row in final['draft_report']['facts']),
                         [('000016.SH', 'close', '2026-10-30', 2742.1108),
                          ('000300.SH', 'close', '2026-10-30', 3985.501)])
        self.assertNotIn('开盘点位', final['draft_report']['conclusion'])
        self.assertNotIn('最高点位', final['draft_report']['conclusion'])

    def test_missing_end_preserves_available_readout_without_substitution(self):
        final = self.replay('比较510300.SH在2026年10月22日和2026年10月23日的收盘价及区间涨跌幅。')
        report = final['draft_report']
        self.assertEqual([(row['period'], row['raw_value']) for row in report['facts']], [(LATE, 4.107)])
        self.assertFalse(report['calculations'])
        self.assertIn('区间收盘涨跌幅暂缺', ' '.join(report['reader_notes']))
        self.assertNotIn('上一交易日', report['conclusion'])

    def test_explicit_daily_and_frozen_previous_policy_remain_distinct(self):
        daily = f'分别核对{SUBJECT}的收盘价和各自日涨跌幅。'
        for question, policy in ((daily, None), (PAIR, 11)):
            with self.subTest(policy=policy):
                task = self.replay(question, policy=policy)
                self.assertFalse(stated_close_pairs(task))
                values = task['draft_report']['calculations']
                self.assertEqual([(row['window_end'], row['display'], row['comparison_kind']) for row in values],
                                 [(EARLY, '-1.21', 'previous_session'), (LATE, '-1.86', 'previous_session')])
        priced_only = self.task(f'核对{SUBJECT}的收盘价，不计算两日之间的涨跌幅。')
        self.assertFalse(stated_close_pairs(priced_only))
        self.assertFalse(stated_close_returns(priced_only))
        analyzed = self.task(PAIR.removesuffix('。')+'，并解释影响。')
        self.assertIsNone(direct_readout(analyzed))
