"""Presentation-only gap slots; no synthetic price becomes evidence."""
import copy
import unittest
from researchhelper.visual import calendar_observation_figure


class CalendarChartSlotTests(unittest.TestCase):
    def setUp(self):
        self.figure = dict(kind='observations', presentation='chart', objects=['510500.SH'],
            periods=['2026-08-03', '2026-08-06'], refs=['e1', 'e2', 'e3'], note='原始适用限制。',
            rows=[dict(label=day, value=value, ref=ref) for day, value, ref in (
                ('2026-08-03', 7.42, 'e1'), ('2026-08-05', 7.51, 'e2'), ('2026-08-06', 7.55, 'e3'))],
            categories=['2026-08-03', '2026-08-05', '2026-08-06'])
        self.panel = dict(artifact_id='price_source', object='510500.SH', status='存在未观测交易日',
            start_date='2026-08-03', end_date='2026-08-07', unexpected_dates=[],
            calendar_ref=dict(artifact_id='calendar_source', snapshot_hash='synthetic_hash'),
            calendar_dates=['2026-08-03', '2026-08-04', '2026-08-05', '2026-08-06', '2026-08-07'])
        self.content = dict(market_calendar_coverage_version=1,
            evidence=[dict(id=ref, artifact_id='price_source') for ref in self.figure['refs']],
            market_coverage=dict(version=1, panels=[self.panel]))

    def test_missing_session_is_only_an_axis_slot_not_an_observation(self):
        before = copy.deepcopy((self.content, self.figure))
        result = calendar_observation_figure(self.content, self.figure)
        self.assertEqual(result['categories'], self.panel['calendar_dates'][:-1])
        self.assertEqual(result['calendar_gap_dates'], ['2026-08-04'])
        self.assertEqual(result['rows'], self.figure['rows'])
        self.assertEqual(result['refs'], self.figure['refs'])
        self.assertIn('图中留空，不补值', result['note'])
        self.assertEqual((self.content, self.figure), before)

    def test_complete_query_window_keeps_unobserved_endpoint(self):
        result = calendar_observation_figure(self.content, self.figure, full_query_window=True)
        self.assertEqual(result['calendar_gap_dates'], ['2026-08-04', '2026-08-07'])
        self.assertEqual(result['categories'], self.panel['calendar_dates'])
        self.assertEqual(len(result['rows']), 3)

    def test_unknown_calendar_adds_no_weekdays_or_prices(self):
        self.panel.update(calendar_ref=None, calendar_dates=[], status='日历未核验')
        result = calendar_observation_figure(self.content, self.figure)
        self.assertEqual(result['categories'], self.figure['categories'])
        self.assertNotIn('calendar_gap_dates', result)
        self.assertIn('不能据此确认区间完整性', result['note'])

    def test_calendar_of_another_artifact_or_object_is_not_borrowed(self):
        for field, value in (('artifact_id', 'foreign_source'), ('object', '159915.SZ')):
            content = copy.deepcopy(self.content)
            content['market_coverage']['panels'][0][field] = value
            self.assertEqual(calendar_observation_figure(content, self.figure), self.figure)

    def test_unexpected_existing_observation_is_not_hidden(self):
        self.panel['calendar_dates'].remove('2026-08-05')
        self.panel['unexpected_dates'] = ['2026-08-05']
        result = calendar_observation_figure(self.content, self.figure)
        self.assertIn('2026-08-05', result['categories'])
        self.assertEqual(result['rows'][1]['value'], 7.51)

    def test_legacy_and_nonseries_charts_remain_unchanged(self):
        content = dict(self.content); content.pop('market_calendar_coverage_version')
        self.assertEqual(calendar_observation_figure(content, self.figure), self.figure)
        for kind in ('points', 'bar'):
            figure = dict(self.figure, kind=kind)
            self.assertEqual(calendar_observation_figure(self.content, figure), figure)


if __name__ == '__main__':
    unittest.main()
