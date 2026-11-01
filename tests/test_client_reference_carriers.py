"""Presentation-only checks on actual saved content; frozen output unchanged."""
import copy
import hashlib
from pathlib import Path
import json
import unittest
from researchhelper.reports import render

ROOT=Path(__file__).resolve().parents[1]
CASE=ROOT/'result/acceptance/default-app-investment-resume-vtrz1pya'


class ClientReferenceCarrierTests(unittest.TestCase):
    def setUp(self):
        self.original=(CASE/'saved-limited-report.html').read_bytes()
        task=json.loads((CASE/'task.json').read_text())
        from researchhelper.reports import compose
        self.content=compose(task)

    def test_same_carrier_citations_all_remain_in_linked_record(self):
        paper=render(self.content,ROOT)
        main=paper.split('</main>',1)[0]
        self.assertTrue('数据依据' in main,'同载体引用应聚合')
        self.assertTrue('>证据61</a>' not in main,'主报告不逐条平铺长序列引用')
        for item in self.content['evidence']:
            self.assertTrue("id='ev-"+item['id']+"'" in paper,'证据原件保留')
            self.assertTrue("href='#ev-"+item['id']+"'" in paper,'证据引用保留')

    def test_client_limits_keep_each_pending_question_not_raw_dispatch_logs(self):
        paper=render(self.content,ROOT);main=paper.split('</main>',1)[0]
        self.assertTrue('本报告不能替代完整投资判断' in main,'受限性质必须明确')
        self.assertTrue('研究限制' in main,'未完成内容仍在主报告')
        self.assertTrue('dr_filing_' not in main,'运行编号放到核验记录')
        for q in self.content['questions']:
            if q['status']!='已回答':self.assertTrue(q['question'] in paper,'完整问题保留')
            if q['status']!='已回答' and q['kind']!='investment':
                self.assertTrue(q['question'] in main,'依赖资料不足也不隐藏反证等必要问题')
        for message in self.content['limitations']:self.assertTrue(message in paper,'原始诊断不可删除')
        self.assertTrue('research-run-diagnostics' in main,'诊断可直接打开')

    def test_no_accordion_or_frozen_content_mutation(self):
        before=copy.deepcopy(self.content)
        paper=render(self.content,ROOT)
        self.assertTrue('<details' not in paper,'不使用折叠报告正文')
        self.assertEqual(self.content,before)
        self.assertEqual((CASE/'saved-limited-report.html').read_bytes(),self.original)

    def test_actual_observation_and_calculation_ids_unchanged(self):
        paper=render(self.content,ROOT)
        for item in self.content['calculations']:self.assertTrue(item['id'] in paper,'计算引用保留')
        for item in self.content['evidence']:self.assertTrue(item['id'] in paper,'全部原始观测保留')


if __name__=='__main__':unittest.main()
