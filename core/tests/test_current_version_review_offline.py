"""Current preflight/report contracts and an explicit counter-evidence update.

Historical tests stay intact. The two overrides follow the adopted early input
rejection and no-visible-directory report design; all other assertions inherit.
"""
import copy
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from tests import test_version_review_offline as original


class CurrentVersionReviewTests(original.VersionReviewTests):
    def test_failed_third_update_does_not_show_second_updates_diff(self):
        second = self.update(self.complete())
        prior = copy.deepcopy(self.agent.archive.get(second['id']))
        reports = [self.agent.archive.report(second['id'], version) for version in (1, 2)]
        with self.assertRaisesRegex(ContractError, 'evidence_specs'):
            self.agent.update(second['id'], cutoff='2026-09-20T18:00:00+08:00',
                              host_result={'evidence_specs': 'invalid'})
        self.assertEqual(self.agent.archive.get(second['id']), prior)
        self.assertEqual([self.agent.archive.report(second['id'], version) for version in (1, 2)], reports)

    def test_report_has_named_versions_full_original_rule_and_toc(self):
        from lxml import html
        before = self.complete(dict(original.RULE, benchmark='<原条件>'))
        prior = self.agent.archive.report(before['id'], 1)
        after = self.update(before)
        tree = html.document_fromstring(self.agent.export(after['id'])['html'])
        self.assertIn('对比发布版本1', tree.get_element_by_id('changes').text_content())
        self.assertIn('本版适用资格解释', tree.find('.//main').text_content())
        self.assertTrue(tree.xpath('//nav//a[@href="#changes"]'))
        conditions = tree.get_element_by_id('version-original-conditions')
        self.assertIn('<原条件>', conditions.text_content())
        self.assertFalse(conditions.xpath('.//原条件'))
        frozen = self.agent.archive.report(after['id'], 2)
        self.assertEqual(frozen['content']['changes']['prior_basis']['theses'][0]['title'],'原版适用资格解释')
        self.assertEqual(self.agent.archive.report(before['id'],1),prior)
        self.assertEqual(fingerprint(frozen['content']),after['delivery']['digest'])

    def test_third_version_save_failure_keeps_history_and_can_retry_without_new_research(self):
        second = self.update(self.complete())
        reports = [self.agent.archive.report(second['id'], version) for version in (1, 2)]
        with patch.object(self.agent.archive, 'deliver', side_effect=OSError('合成保存失败')):
            failed = self.agent.update(second['id'], cutoff='2026-09-20T18:00:00+08:00',
                                       host_result=self.host_result(second))
        self.assertEqual(failed['status'], '失败')
        self.assertEqual(failed['changes']['prior_version'], 2)
        self.assertEqual(failed['changes']['research_version'], 3)
        with self.assertRaises(ContractError):
            self.agent.archive.report(second['id'], 3)
        self.assertEqual([self.agent.archive.report(second['id'], version) for version in (1, 2)], reports)
        used = copy.deepcopy(failed['ledger']['used'])
        restarted = ResearchAgent(ResearchArchive(self.agent.archive.root), self.agent.resources, offline=True)
        saved = restarted.retry_save(second['id'])
        self.assertEqual(saved['version'], 3)
        for field in ('data_requests', 'model_calls'):
            self.assertEqual(saved['ledger']['used'][field], used[field])
        self.assertGreaterEqual(saved['ledger']['used']['seconds'], used['seconds'])
        self.assertEqual([restarted.archive.report(second['id'], version) for version in (1, 2)], reports)

    def test_new_counter_evidence_changes_judgment_without_rewriting_old_conditions(self):
        before = self.complete()
        old = self.agent.archive.report(before['id'], 1)
        task = self.agent.prepare_update(before['id'], '2026-08-20T18:00:00+08:00')
        text = ('材料示例政策修订\n\n发布日期：2026年8月19日。\n\n'
                '政策自2026年7月21日起生效。\n\n原适用资格已撤销，不再适用于相关企业。')
        task = self.agent.add_material(task['id'], 'policy-revision.txt', text.encode())
        newest = task['materials'][-1]
        specs = self.fixture.specs(dict(task, materials=[newest]))
        for spec in specs:
            spec['published_at'] = '2026-08-19'
            spec['publication']['at'] = '2026-08-19'
            spec['publication']['basis_ref']['quote'] = '发布日期：2026年8月19日。'
        specs[1]['text'] = '原适用资格已撤销，不再适用于相关企业。'
        specs[1]['review']['reason'] = '修订原文明确撤销原适用资格，不延用旧版资格判断'
        thesis = self.fixture.thesis('反对')
        thesis.update(title='原适用资格论点被修订条款反对', support=['effective_date'],
                      counter=['eligibility'], tracking=None)
        from researchhelper.judgment_review import contract
        anchor=contract(task)['anchors'][0]
        review=dict(prior_thesis_id=anchor['id'],prior_thesis_hash=anchor['thesis_hash'],
            prior_version=task['update_from']['version'],conditions_hash=anchor['conditions_hash'],
            assessment='调整',current_thesis_indices=[0],dimensions=dict(
                facts=dict(state='依据有变化',answer='新版原件明确撤销原资格。',refs=['eligibility'],calculation_refs=[]),
                mechanism=dict(state='反对',answer='撤销条款不再支持原适用资格解释。',refs=['eligibility'],calculation_refs=[]),
                expectations=dict(state='不适用',answer='本问题不比较预测与实际结果。',refs=[],calculation_refs=[]),
                price=dict(state='不适用',answer='本问题没有研究市场价格。',refs=[],calculation_refs=[])))
        done = self.agent.start(task['id'], {'evidence_specs': specs,
            'answers': {'q1': {'answer': '政策自2026年7月21日起生效；原适用资格已撤销。',
                              'refs': ['effective_date', 'eligibility'], 'calculation_refs': []}},
            'theses': [thesis],'judgment_reviews':[review]})
        self.assertEqual(done['status'], '已完成', done.get('error') or done.get('gaps'))
        self.assertEqual(len(done['judgment_reviews']),1)
        self.assertEqual(done['judgment_reviews'][0]['dimensions']['price']['state'],'不适用')
        self.assertEqual(done['theses'][0]['judgment'], '反对')
        self.assertEqual(done['theses'][0]['counter'], ['eligibility'])
        self.assertEqual(done['changes']['prior_judgments'], ['支持'])
        self.assertEqual(done['changes']['current_judgments'], ['反对'])
        self.assertIn('eligibility', done['changes']['revised'])
        self.assertEqual(done['changes']['original_conditions'], before['tracking'])
        self.assertEqual(self.agent.archive.report(before['id'], 1), old)
        frozen = self.agent.archive.report(before['id'], 2)
        self.assertEqual(frozen['content']['theses'][0]['counter'], ['eligibility'])
        self.assertIn(specs[1]['text'], frozen['html'])
        self.assertEqual(done['ledger']['used']['data_requests'], 0)
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
