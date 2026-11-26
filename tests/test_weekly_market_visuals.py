"""Weekly bulletin graphics must remain bound to each argument's cited cells."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from core.session import Session
from researchhelper.draft_visuals import logic_figure_plan
from researchhelper.layout import default_resource_root
from researchhelper.report_designer import design, read_presentation_document


INDEXES = '指数一周涨跌幅：上证综指-2.60%，上证180指数-0.45%，科创50指数-3.20%。'
CURRENT = '股票周成交额：沪市主板22,004亿元，科创板11,405亿元。'
PREVIOUS = '股票周成交额：沪市主板24,676亿元，科创板12,780亿元。'


class WeeklyMarketVisualTests(unittest.TestCase):
    def setUp(self):
        project = TemporaryDirectory(prefix='weekly-market-')
        self.addCleanup(project.cleanup)
        self.session = Session(Path(project.name))
        self.addCleanup(self.session.agent.close_runtime)
        task = self.session.create('复盘沪市一周行情及分板块成交情况。', entry='指数', research_mode='current')
        self.task_id = task['id']
        current = self.add_summary('本周数据.txt', '2025年11月17日至21日', INDEXES + '\n' + CURRENT, '123')
        previous = self.add_summary('前周数据.txt', '2025年11月10日至14日', PREVIOUS, '456')
        report = dict(title='沪市周度观察', conclusion='成长回撤，成交走低。',
            logics=[
                dict(title='宽基与成长分化', analysis=INDEXES + '\n成长指数回撤更深。', source_refs=[current]),
                dict(title='分板块成交下降', analysis=CURRENT + '\n' + PREVIOUS + '\n两个板块的成交额均下降。',
                     source_refs=[previous, current]),
            ])
        self.session.draft(self.task_id, report=report, data_requests=[])
        self.content = self.session.archive.report(self.task_id, 1)['content']

    def add_summary(self, name, period, values, source_number):
        body = ('上海证券交易所，沪市市场运行情况例行发布，统计期' + period + '。\n' + values
                + '\n公开原件：https://www.sse.com.cn/aboutus/mediacenter/conference/c/' + source_number + '/files/abc.pdf')
        return self.session.material(self.task_id, name, body.encode())['id']

    def test_document_comparisons_have_the_exact_unit_periods_and_values(self):
        indexes, turnover = logic_figure_plan(self.content)
        self.assertEqual(indexes['auto_visual']['scheme'], 'diverging_bar')
        self.assertEqual(indexes['data']['values'], [-.45, -2.6, -3.2])
        self.assertEqual(turnover['auto_visual']['scheme'], 'grouped_bar')
        self.assertEqual(turnover['data']['unit'], '亿元')
        self.assertEqual([row['values'] for row in turnover['data']['series']], [[24676, 12780], [22004, 11405]])
        self.assertEqual(len(turnover['source_refs']), 2)
        self.assertEqual(self.session.status(self.task_id)['ledger']['used']['data_requests'], 0)

    def test_standard_uses_figures_and_keeps_every_raw_source_in_the_inspector(self):
        figures = []
        for profile in ('standard', 'detailed'):
            page = design(self.content, default_resource_root(), profile=profile)['html']
            tree, report = read_presentation_document(page)
            charts = [section['spec']['auto_visual']['scheme'] for section in report['blocks'] if section['kind'] == 'chart']
            figures.append(charts)
            main = tree.find('.//main')
            body = main.text_content()
            self.assertEqual('22,004亿元' in body, profile == 'detailed')
            self.assertIn('两个板块的成交额均下降', body)
            note = tree.get_element_by_id('draft-figure-basis-logic-2-chart-1')
            self.assertIn('22,004亿元', note.text_content())
            self.assertIn('12,780亿元', note.text_content())
            self.assertEqual(len(note.xpath('.//a[@href]')), 2)
        self.assertEqual(figures, [['diverging_bar', 'grouped_bar']] * 2)

    def test_missing_citation_changed_snapshot_and_historical_scope_leave_prose(self):
        for failure in ('uncited', 'snapshot', 'historical'):
            with self.subTest(failure=failure):
                changed = deepcopy(self.content)
                if failure == 'uncited':
                    links = changed['draft']['logics'][1]['quantity_links']['analysis']['links']
                    links.pop()
                elif failure == 'snapshot':
                    changed['materials'][0]['snapshot']['parts'][0]['text'] += '\n更改内容'
                else:
                    changed['research_mode'] = 'historical'
                plots = logic_figure_plan(changed)
                self.assertFalse(any(plot['paper_logic'] == 'logic-2' for plot in plots))


if __name__ == '__main__':
    unittest.main()
