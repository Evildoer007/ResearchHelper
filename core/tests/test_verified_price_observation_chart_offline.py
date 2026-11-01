"""Plot only acquired, individually verified prices with their original rows."""
import copy
import unittest

from researchhelper.visual import research_figures


def fixture():
    dates = ['2026-09-21', '2026-09-22', '2026-09-24', '2026-09-30']
    values = [10.0, 11.0, 9.0, 13.0]
    source = {'id': 'source1', 'kind': 'api', 'source': 'ifind',
              'operation': 'cmd_history_quotation', 'hash': 'bound-source-hash',
              'task_id': 'rh_test', 'research_version': 1,
              'parameters': {'codes': '510300.SH', 'indicators': 'close',
                             'functionpara': {'Currency': 'RMB', 'CPS': '1'}},
              'snapshot': {'tables': [{'thscode': '510300.SH', 'time': dates,
                                        'table': {'close': values}}]}}
    context = {'security_id': '510300.SH', 'currency': 'CNY', 'price_basis': 'unadjusted',
               'price_kind': 'close', 'window_start': dates[0], 'window_end': dates[-1],
               'corporate_action_status': 'unknown'}
    evidence = []
    for index, (day, value) in enumerate(zip(dates, values)):
        record = {'id': 'ev' + str(index), 'artifact_id': source['id'], 'snapshot_hash': source['hash'],
                  'task_id': source['task_id'], 'research_version': 1, 'source': 'ifind',
                  'source_check': '通过', 'applicability_check': '通过', 'nature': '已实现事实',
                  'object': '510300.SH', 'period': day, 'unit': '元', 'raw_unit': '元',
                  'conversion_factor': 1, 'value': value, 'raw_value': value, 'question_ids': ['q1'],
                  'record_locator': {'table': 0, 'row': index, 'field': 'close'}}
        if index in (0, len(dates) - 1):
            record['calculation_context'] = dict(context, observation_date=day)
        evidence.append(record)
    calculation = {'id': 'change', 'method': 'observed_price_return_v1',
                   'inputs': {'start_price': values[0], 'end_price': values[-1]},
                   'input_bindings': {'start_price': 'ev0', 'end_price': 'ev3'},
                   'input_refs': ['ev0', 'ev3'], 'question_ids': ['q1'],
                   'parameters': {'start_date': dates[0], 'end_date': dates[-1], 'unit': '元',
                                  'currency': 'CNY', 'price_basis': 'unadjusted', 'return_kind': 'price_change'},
                   'output': {'unit': '%', 'value': 30.0}}
    return {'task_id': 'rh_test', 'research_version': 1, 'source_records': [source],
            'evidence': evidence, 'calculations': [calculation], 'theses': []}


class VerifiedPriceObservationChartTests(unittest.TestCase):
    def test_all_verified_observations_and_refs_are_preserved_without_fill(self):
        content = fixture()
        before = copy.deepcopy(content)
        figure = research_figures(content)['change']
        self.assertEqual(figure['kind'], 'observations')
        self.assertEqual(figure['categories'], content['source_records'][0]['snapshot']['tables'][0]['time'])
        self.assertEqual([row['value'] for row in figure['rows']], [10.0, 11.0, 9.0, 13.0])
        self.assertEqual(figure['refs'], ['ev0', 'ev1', 'ev2', 'ev3'])
        self.assertIn('不补齐缺失日期', figure['note'])
        self.assertIn('公司行为', figure['note'])
        self.assertEqual(content, before)

    def test_two_verified_endpoints_keep_the_original_point_chart(self):
        content = fixture()
        content['evidence'] = [content['evidence'][0], content['evidence'][-1]]
        figure = research_figures(content)['change']
        self.assertEqual(figure['kind'], 'points')
        self.assertEqual(figure['refs'], ['ev0', 'ev3'])
        self.assertEqual(len(figure['rows']), 2)

    def test_unrelated_unverified_or_other_snapshot_observations_are_not_added(self):
        mutations = (
            lambda item: item.update(snapshot_hash='foreign'),
            lambda item: item.update(task_id='foreign'),
            lambda item: item.update(research_version=2),
            lambda item: item.update(source_check='待核验'),
            lambda item: item.update(applicability_check='待核验'),
            lambda item: item.update(question_ids=['other']),
            lambda item: item.update(nature='预测'),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                content = fixture()
                mutation(content['evidence'][1])
                figure = research_figures(content)['change']
                self.assertNotIn('ev1', figure['refs'])
                self.assertNotIn('2026-09-22', figure['categories'])

    def test_inconsistent_original_value_or_date_falls_back_to_endpoints(self):
        for field, value in (('value', 999), ('period', '2026-09-23'), ('conversion_factor', 100)):
            with self.subTest(field=field):
                content = fixture()
                content['evidence'][1][field] = value
                self.assertEqual(research_figures(content)['change']['kind'], 'points')

    def test_unsupported_request_or_missing_origin_cannot_make_a_daily_path(self):
        for mode in ('adjusted', 'currency', 'provider', 'missing'):
            content = fixture()
            source = content['source_records'][0]
            if mode == 'adjusted':
                source['parameters']['functionpara']['CPS'] = '2'
            elif mode == 'currency':
                source['parameters']['functionpara']['Currency'] = 'USD'
            elif mode == 'provider':
                source['source'] = 'unregistered'
            else:
                content.pop('source_records')
            with self.subTest(mode=mode):
                self.assertEqual(research_figures(content)['change']['kind'], 'points')

    def test_conflicting_duplicate_dates_cannot_be_arbitrarily_selected(self):
        content = fixture()
        duplicate = copy.deepcopy(content['evidence'][1])
        duplicate['id'] = 'duplicate'
        content['evidence'].append(duplicate)
        self.assertEqual(research_figures(content)['change']['kind'], 'points')
