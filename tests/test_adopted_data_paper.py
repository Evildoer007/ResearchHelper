"""Offline data-first paper from a closed failed-analysis macro study.

Only presentation is exercised. The old task, report, failed review and model
balance error remain unchanged; no research is resumed or called successful.
"""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from tests import ROOT, read_retained_export
from tests.test_partial_report_collection import MainPaper
from researchhelper.contracts import fingerprint
from researchhelper.reports import render
from researchhelper.visual import adopted_numeric_evidence,pmi_observation_figures

FIXTURE=ROOT/'result/acceptance/one-sentence-research-1cr8hfby/report.json'


class AdoptedDataPaperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw=read_retained_export(FIXTURE);cls.before=hashlib.sha256(raw).hexdigest();cls.content=json.loads(raw)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(read_retained_export(FIXTURE)).hexdigest(),self.before)

    def test_actual_adopted_values_survive_without_an_approved_answer(self):
        self.assertFalse(any(q.get('answer') for q in self.content['questions']))
        self.assertEqual(len(adopted_numeric_evidence(self.content)),12)
        with patch('socket.socket.connect',side_effect=AssertionError('Offline only')):
            page=render(self.content,ROOT)
        text=''.join(MainPaper(page).text)
        for expected in ('核验数据','50.1%','51.7%','50.5%','2026-09','2026-08','研究分析尚未完成'):
            self.assertIn(expected,text)
        self.assertIn('单位：%',text)
        self.assertNotIn('单位：元',text)
        self.assertNotIn('<details',page)
        self.assertEqual(len(self.content['evidence']),14)

    def test_actual_pmi_figure_uses_six_exact_adopted_values_without_a_result(self):
        before=copy.deepcopy(self.content)
        figures=pmi_observation_figures(self.content)
        self.assertEqual(len(figures),1)
        figure=figures[0]
        self.assertEqual(figure['template'],'rh-pmi-comparison-1')
        self.assertEqual(figure['kind'],'levels')
        self.assertEqual(figure['periods'],['2026-08','2026-09'])
        self.assertEqual([row['value'] for row in figure['rows']],[49.8,50.4,50.6,50.1,51.7,50.5])
        self.assertEqual(figure['unit'],'%')
        self.assertEqual(len(set(figure['refs'])),6)
        self.assertNotIn('result_value',figure)
        self.assertNotIn('benchmark',figure)
        self.assertEqual(self.content,before)

    def test_missing_metric_never_becomes_zero_or_a_fabricated_comparison(self):
        content=copy.deepcopy(self.content)
        content['evidence']=[item for item in content['evidence'] if item['id']!='neworders_aug2026']
        self.assertEqual(pmi_observation_figures(content),[])
        self.assertEqual(len(adopted_numeric_evidence(content)),11)

    def test_foreign_failed_or_changed_snapshot_is_not_charted(self):
        for change in ('foreign','failed','changed'):
            content=copy.deepcopy(self.content)
            for item in content['evidence']:
                if change=='foreign':item['task_id']='another-task'
                elif change=='failed':item['source_check']='待核验'
                elif 'artifact' in item:item['artifact']['snapshot']['extra']='changed'
            with self.subTest(change=change):self.assertEqual(pmi_observation_figures(content),[])

    def test_non_pmi_objects_and_growth_units_are_not_relabeled(self):
        content=copy.deepcopy(self.content)
        for item in content['evidence']:
            item['object']='居民消费价格同比';item['unit']='个百分点'
        self.assertEqual(pmi_observation_figures(content),[])

    def test_changed_numeric_value_does_not_inherit_old_approval(self):
        content=copy.deepcopy(self.content)
        for item in content['evidence']:
            if type(item.get('value')) in (int,float):item['value']+=1
        self.assertEqual(adopted_numeric_evidence(content),[])
        self.assertEqual(pmi_observation_figures(content),[])

    def test_explicit_answer_references_do_not_draw_uncited_data(self):
        ids={item['id'] for item in self.content['evidence'] if item['period']=='2026-09'}
        self.assertEqual(pmi_observation_figures(self.content,ids),[])

    def test_frozen_content_and_rejected_prose_are_untouched(self):
        before=fingerprint(self.content)
        page=render(self.content,ROOT)
        self.assertEqual(fingerprint(self.content),before)
        self.assertIn('data-reader-version',page)
        self.assertFalse(any(q.get('answer') for q in self.content['questions']))


if __name__=='__main__':unittest.main()
