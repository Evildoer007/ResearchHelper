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
        before = self.complete(dict(original.RULE, benchmark='<原条件>'))
        after = self.update(before)
        paper = self.agent.export(after['id'])['html']
        for text in ('版本1判断', '版本2判断', '原版适用资格解释', '本版适用资格解释',
                     '下一次主动更新', '&lt;原条件&gt;', "id='changes'"):
            self.assertIn(text, paper)
        self.assertNotIn("href='#changes'", paper)
        self.assertEqual(fingerprint(self.agent.archive.report(after['id'], 2)['content']), after['delivery']['digest'])

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
        done = self.agent.start(task['id'], {'evidence_specs': specs,
            'answers': {'q1': {'answer': '政策自2026年7月21日起生效；原适用资格已撤销。',
                              'refs': ['effective_date', 'eligibility'], 'calculation_refs': []}},
            'theses': [thesis]})
        self.assertEqual(done['status'], '已完成', done.get('error') or done.get('gaps'))
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
