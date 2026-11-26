"""Selection, ordinary report freezing and chart projection; no real study run."""
import copy
import importlib
import json
import os
from pathlib import Path
import unittest
from researchhelper.contracts import fingerprint
from researchhelper.evidence import api_cell_choices, expand_api_cell_selections
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.output_schema import api_extraction_output_schema, decode_api_extraction_output
from researchhelper.reasoning import extractable_source_views, validate_model_specs
from researchhelper.report_designer import design
from researchhelper.reports import compose, check_content
from researchhelper.visual import economic_observation_figures

_prefix = __package__+'.' if __package__ else ''
fixture = importlib.import_module(_prefix+'test_economic_observations')


def adopted_task():
    helper = fixture.EconomicObservationTests()
    helper.setUp()
    try:
        task = helper.task
        task['evidence'] = [helper.adopted(choice) for choice in api_cell_choices(task, helper.record)]
        task['status'] = '部分完成'
        task['gaps'] = ['仅进行数据核验和离线呈现，未执行完整研究分析。']
        task['knowledge'] = ResearchKnowledge().read(task['entry'], task['question'], context=task)
        return copy.deepcopy(task)
    finally:
        helper.tearDown()
        helper.doCleanups()


def wire_selection(task, views):
    schema = api_extraction_output_schema(task, views)
    source = next(row for row in task['source_records'] if row['operation'] == 'edb_service')
    ids = [row['candidate_id'] for row in api_cell_choices(task, source)]
    review = dict(allowed_natures=['已实现事实'], conditions=['当前取得的月度统计值，不作历史首发。'],
        reason='核对对应月度CPI当月同比的实际观测。')
    version = schema['properties']['version']['enum'][0]
    group = dict(api_cell_selections=ids, review=review)
    if version == 3:
        review['need_links'] = [dict(question_id='q1', need_ids=['n1'])]
    else:
        group['question_ids'] = ['q1']
        review.update(question_ids=['q1'], evidence_need_texts=[dict(question_id='q1', texts=['月度CPI当月同比'])])
    return dict(version=version, evidence_specs=[group]), schema


class EconomicObservationDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.task = adopted_task()
        self.content = compose(self.task)
        self.question = dict(id='q1', status='已回答', refs=[item['id'] for item in self.task['evidence']])

    def test_ordinary_compose_freezes_nine_facts_without_claiming_completed_research(self):
        check_content(self.content)
        self.assertEqual(len(self.content['evidence']), 9)
        self.assertEqual(self.content['economic_observation_version'], 1)
        self.assertTrue(all(q['status'] != '已回答' for q in self.content['questions']))
        self.assertFalse(self.content['theses'])
        self.assertEqual(self.content['source_records'], self.task['source_records'])

    def test_compact_api_protocol_reconstructs_the_actual_statistics_not_price_fields(self):
        task = copy.deepcopy(self.task)
        task['evidence'] = []
        task['api_cell_selection_encoding'] = 1
        task['input_hash'] = fixture.input_fingerprint(task)
        views = extractable_source_views(task)
        wire, schema = wire_selection(task, views)
        self.assertIsNotNone(schema)
        decoded = decode_api_extraction_output(wire, schema, (task, views))
        expanded = expand_api_cell_selections(task, decoded['evidence_specs'])
        self.assertEqual(len(expanded), 9)
        self.assertTrue(all(row['economic_context']['frequency'] == 'M' for row in expanded))
        self.assertTrue(all('calculation_context' not in row for row in expanded))
        self.assertTrue(all('field_definition' not in row for row in expanded))
        validate_model_specs(task, decoded['evidence_specs'], schema_only=True)
        validate_model_specs(task, decoded['evidence_specs'])

    def test_chart_has_nine_actual_sorted_months_without_derived_values(self):
        before = fingerprint(self.content)
        figure = economic_observation_figures(self.content, self.question)[0]
        self.assertEqual(figure['kind'], 'observations')
        self.assertEqual(figure['unit'], '%')
        self.assertEqual(figure['series_color'], 'blue')
        self.assertEqual(figure['categories'], [f'2026-{month:02d}' for month in range(1, 10)])
        self.assertEqual([row['value'] for row in figure['rows']], [0.2, 1.3, 1, 1.2, 1.2, 1, 0.5, 0.8, 0.9])
        self.assertEqual(len(figure['refs']), 9)
        self.assertNotIn('result_value', figure)
        self.assertEqual(fingerprint(self.content), before)

    def test_missing_month_is_not_zero_filled_or_connected_as_a_trend(self):
        content = copy.deepcopy(self.content)
        content['evidence'] = [item for item in content['evidence'] if item['period'] != '2026-04']
        figure = economic_observation_figures(content, self.question)[0]
        self.assertEqual(figure['kind'], 'levels')
        self.assertEqual(len(figure['rows']), 8)
        self.assertNotIn('2026-04', figure['categories'])

    def test_unanswered_question_has_no_conclusion_chart_but_data_preview_is_explicit(self):
        question = dict(self.question, status='未回答')
        self.assertEqual(economic_observation_figures(self.content, question), [])
        self.assertEqual(len(economic_observation_figures(self.content, question, adopted_only=True)), 1)

    def test_tampered_unapproved_and_foreign_facts_do_not_enter_figures(self):
        for key, value in (('source_check', '待核验'), ('task_id', 'foreign'), ('value', 999), ('unit', '亿元')):
            content = copy.deepcopy(self.content)
            for item in content['evidence']:
                item[key] = value
            self.assertEqual(economic_observation_figures(content, self.question), [])

    def test_partial_report_is_rendered_without_folding_or_mutating_its_content(self):
        before = fingerprint(self.content)
        resources = Path(os.environ.get('RESEARCHHELPER_IFIND_METADATA_RESOURCES', fixture.ROOT))
        result = design(self.content, resources)
        page = result['html']
        self.assertIn('rh-economic-observations-1', page)
        self.assertIn('研究分析尚未完成', page)
        self.assertNotIn('<details', page)
        doc = result['document']
        chart, = (block['spec'] for block in doc['blocks'] if block['kind'] == 'chart')
        self.assertEqual(doc['visual_sources'][chart['target']], chart)
        self.assertEqual(chart['auto_visual']['data']['values'], [0.2, 1.3, 1, 1.2, 1.2, 1, 0.5, 0.8, 0.9])
        self.assertEqual(fingerprint(self.content), before)

    def test_uncited_or_legacy_content_does_not_inherit_the_statistical_chart(self):
        self.assertEqual(economic_observation_figures(self.content, dict(self.question, refs=[])), [])
        content = copy.deepcopy(self.content)
        content.pop('economic_observation_version')
        self.assertEqual(economic_observation_figures(content, self.question), [])
