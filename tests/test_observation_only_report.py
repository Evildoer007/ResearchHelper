"""Charts may display verified prices without requiring a fabricated return."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core/tests'))
from test_verified_price_observation_chart_offline import fixture
from researchhelper.visual import question_observation_figures


class ObservationOnlyReportTests(unittest.TestCase):
    def setUp(self):
        self.content = fixture()
        self.content['calculations'] = []
        self.question = {'id': 'q1', 'status': '已回答', 'refs': [e['id'] for e in self.content['evidence']]}

    def test_actual_rows_without_a_calculation_are_drawn_without_mutation(self):
        original = copy.deepcopy(self.content)
        figure = question_observation_figures(self.content, self.question)[0]
        self.assertEqual([row['value'] for row in figure['rows']], [10, 11, 9, 13])
        self.assertEqual(figure['categories'], ['2026-09-21', '2026-09-22', '2026-09-24', '2026-09-30'])
        self.assertNotIn('result_value', figure)
        self.assertEqual(self.content, original)

    def test_missing_day_is_not_filled(self):
        figure = question_observation_figures(self.content, self.question)[0]
        self.assertNotIn('2026-09-23', figure['categories'])

    def test_foreign_task_or_source_cannot_enter_the_chart(self):
        for field, value in (('task_id', 'foreign'), ('research_version', 2), ('snapshot_hash', 'foreign')):
            with self.subTest(field=field):
                content = copy.deepcopy(self.content)
                for record in content['evidence']:
                    record[field] = value
                self.assertEqual(question_observation_figures(content, self.question), [])

    def test_mismatched_numeric_value_cannot_create_a_chart(self):
        self.content['evidence'][1]['value'] = 999
        self.assertEqual(question_observation_figures(self.content, self.question), [])

    def test_two_points_do_not_become_a_daily_path(self):
        self.question['refs'] = ['ev0', 'ev3']
        self.assertEqual(question_observation_figures(self.content, self.question), [])

    def test_unanswered_question_does_not_display_a_conclusion_chart(self):
        self.question['status'] = '未回答'
        self.assertEqual(question_observation_figures(self.content, self.question), [])

    def test_adjusted_or_other_currency_is_not_mislabeled_as_cny_unadjusted(self):
        self.content['source_records'][0]['parameters']['functionpara']['Currency'] = 'USD'
        self.assertEqual(question_observation_figures(self.content, self.question), [])

    def test_uncited_records_are_not_added_to_the_graph(self):
        self.question['refs'].remove('ev1')
        figure = question_observation_figures(self.content, self.question)[0]
        self.assertEqual(figure['refs'], ['ev0', 'ev2', 'ev3'])
