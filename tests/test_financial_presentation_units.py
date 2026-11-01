"""Rendered financial metadata matches the validated original representation.

Synthetic financial cases only; no external request or production mutation.
"""
import copy
import sys
from pathlib import Path
import tempfile
import unittest

from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.visual import research_figures

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from test_financial_amount_representations import unit_fixture


class FinancialPresentationUnitTests(unittest.TestCase):
    def test_raw_unit_matches_chart_axis_rows_and_formula_explanation(self):
        for unit in ('元','万元','百万元'):
            with self.subTest(unit=unit), tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance') as folder:
                agent=ResearchAgent(ResearchArchive(folder),ROOT,offline=True)
                task,host=unit_fixture(agent,unit)
                final=agent.start(task['id'],host)
                self.assertEqual(final['status'],'已完成',final.get('error'))
                delivery=agent.export(final['id']);before=copy.deepcopy(delivery['content'])
                for figure in research_figures(delivery['content']).values():
                    self.assertEqual(figure['unit'],unit)
                    self.assertTrue(all(row['unit']==unit for row in figure['rows']))
                paper=delivery['html']
                self.assertTrue('的'+unit+'数据' in paper and '均为'+unit+'；' in paper,
                                'Financial formula labels must match the selected amount unit')
                if unit!='百万元':self.assertTrue('均为百万元' not in paper,
                                               'No stale million-yuan formula label')
                self.assertEqual(delivery['content'],before)

    def test_analyst_projection_retains_separate_financial_period_and_observation_date(self):
        evidence=dict(id='synthetic',object='合成测试',period='2026年上半年',observation_date='2026-06-30',
                      question_ids=['q1'],unit='元',text='合成示例')
        task={'evidence':[evidence]}
        projected=ResearchAgent._reasoning_evidence(task,['q1'])
        self.assertEqual(projected[0]['period'],'2026年上半年')
        self.assertEqual(projected[0]['observation_date'],'2026-06-30')
        self.assertEqual(evidence,task['evidence'][0])


if __name__=='__main__':unittest.main()
