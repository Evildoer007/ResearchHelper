"""Ordinary research obligations, actual originals, no network or model calls."""

import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import (ContractError, evidence_requirements, fingerprint,
    missing_evidence_requirements, validate_evidence)
from researchhelper.engine import ResearchAgent
from researchhelper.reasoning import evidence_contract
from researchhelper.reports import compose
from tests import test_autonomous_reasoning_offline as policy_fixture

ROOT = Path(__file__).resolve().parents[2]
TEXT = '材料示例政策\n\n发布日期：2026年7月19日。\n\n政策自2026年7月21日起生效。\n\n适用对象为符合条件的企业。'
NEEDS = ['生效日期', '适用范围']


class ResearchCoverageTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='research-coverage-')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)

    def task(self, kind='fact', questions=None):
        question = '政策何时生效，适用哪些企业？'
        task = self.agent.create(question, objects=['材料示例政策'], cutoff='2026-07-20T18:00:00+08:00',
            questions=questions or [{'id': 'q1', 'question': question, 'kind': kind, 'evidence_needs': NEEDS}])
        return self.agent.add_material(task['id'], 'policy.txt', TEXT.encode())

    def specs(self, task):
        first = policy_fixture.AutonomousReasoningTests.policy_spec(task)
        first['review']['evidence_need_ids'] = {'q1': [evidence_requirements(task['questions'][0])[0]['id']]}
        second = copy.deepcopy(first)
        second.update(id='eligibility', record_locator={'part': '段落4'}, text='适用对象为符合条件的企业。')
        second['review']['evidence_need_ids'] = {'q1': [evidence_requirements(task['questions'][0])[-1]['id']]}
        second['review']['reason'] = '本段明确列示政策适用对象'
        return [first, second]

    def result(self, task, specs, refs=None, theses=None):
        refs = refs if refs is not None else [item['id'] for item in specs]
        return self.agent.start(task['id'], {'evidence_specs': specs,
            'answers': {'q1': {'answer': '政策自2026年7月21日起生效，适用于符合条件的企业。',
                              'refs': refs, 'calculation_refs': []}}, 'theses': theses or []})

    def thesis(self, judgment='支持', refs=None):
        return {'title': '条款明确限定适用范围', 'judgment': judgment,
                'investment_meaning': '只能讨论符合条件企业，条款不证明未来盈利改善。',
                'limitation': '缺少经营和市场数据，不能推出买卖方向。',
                'support': refs or ['effective_date', 'eligibility'], 'counter': [],
                'question_ids': ['q1'], 'calculation_refs': []}

    def test_registered_requirements_use_stable_ids_across_plan_order(self):
        task = self.task()
        first = {item['text']: item['id'] for item in evidence_requirements(task['questions'][0])}
        task['questions'][0]['evidence_needs'].reverse()
        self.assertEqual(first, {item['text']: item['id'] for item in evidence_requirements(task['questions'][0])})

    def test_complete_originals_and_actual_answer_refs_satisfy_the_plan(self):
        task = self.task()
        done = self.result(task, self.specs(task))
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertEqual(done['questions'][0]['status'], '已回答')
        self.assertEqual(missing_evidence_requirements(done['questions'][0], done['evidence']), [])
        self.assertEqual(done['ledger']['used']['data_requests'], 0)
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertTrue(self.agent.export(task['id'])['html'])

    def test_partial_material_cannot_pass_even_with_a_fluent_complete_sounding_answer(self):
        task = self.task()
        done = self.result(task, self.specs(task)[:1])
        self.assertEqual(done['status'], '部分完成')
        self.assertEqual(done['questions'][0]['status'], '未回答')
        self.assertIn('适用范围', ' '.join(done['gaps']))
        self.assertEqual([len(item['refs']) for item in done['questions'][0]['evidence_coverage']['items']], [1, 0])
        self.assertEqual(len(done['evidence']), 1)
        self.assertEqual(done['questions'][0]['evidence_needs'], NEEDS)

    def test_other_archived_evidence_cannot_replace_omitted_answer_references(self):
        task = self.task()
        done = self.result(task, self.specs(task), refs=['effective_date'])
        self.assertEqual(len(done['evidence']), 2)
        self.assertEqual(done['status'], '部分完成')
        self.assertEqual(done['questions'][0]['status'], '未回答')
        self.assertIn('适用范围', ' '.join(done['gaps']))

    def test_unmapped_useful_excerpts_remain_usable_but_count_no_coverage(self):
        task = self.task()
        specs = self.specs(task)
        for spec in specs:
            spec['review'].pop('evidence_need_ids')
        done = self.result(task, specs)
        self.assertEqual(len(done['evidence']), 2)
        self.assertEqual(done['status'], '部分完成')
        self.assertEqual([item['text'] for item in missing_evidence_requirements(done['questions'][0], done['evidence'])], NEEDS)

    def test_unknown_duplicate_and_foreign_question_links_are_rejected(self):
        for mapping in [{'other': ['unknown']}, {'q1': ['unknown']}, {'q1': []}, {'q1': 'not-list'}]:
            task = self.task()
            specs = self.specs(task)[:1]
            specs[0]['review']['evidence_need_ids'] = mapping
            done = self.result(task, specs)
            self.assertEqual(done['evidence'], [], mapping)
            self.assertTrue(done['rejected_evidence'])
        task = self.task()
        specs = self.specs(task)[:1]
        specs[0]['review']['evidence_need_ids']['q1'] *= 2
        self.assertEqual(self.result(task, specs)['evidence'], [])

    def test_review_mapping_is_bound_into_provenance_not_a_bare_approval(self):
        task = self.task()
        done = self.result(task, self.specs(task))
        item = copy.deepcopy(done['evidence'][0])
        item['applicability_review']['evidence_need_ids']['q1'].append(evidence_requirements(done['questions'][0])[1]['id'])
        with self.assertRaises(ContractError):
            validate_evidence(item, done)

    def test_report_rejects_forged_completion_with_missing_cited_coverage(self):
        task = self.task()
        done = self.result(task, self.specs(task))
        done['questions'][0]['refs'] = ['effective_date']
        with self.assertRaisesRegex(ContractError, '全部依据需求'):
            compose(done)

    def test_optional_coverage_gap_does_not_block_an_independent_core_answer(self):
        task = self.task(questions=[
            {'id': 'q1', 'question': '政策何时生效？', 'kind': 'fact', 'evidence_needs': ['生效日期']},
            {'id': 'q2', 'question': '其他企业受何影响？', 'kind': 'fact', 'required': False,
             'group': 'optional', 'evidence_needs': ['其他企业经营数据']}])
        done = self.result(task, self.specs(task)[:1])
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertEqual(done['questions'][0]['status'], '已回答')
        self.assertEqual(done['questions'][1]['status'], '未回答')
        self.assertTrue(done['optional_findings'])

    def test_supported_thesis_requires_coverage_of_its_own_references(self):
        task = self.task(kind='mechanism')
        done = self.result(task, self.specs(task), theses=[self.thesis(refs=['effective_date'])])
        self.assertEqual(done['theses'], [])
        self.assertEqual(done['status'], '部分完成')
        self.assertIn('判断依据未覆盖', ' '.join(done['gaps']))

    def test_fully_cited_limited_thesis_can_complete_without_adding_valuation(self):
        task = self.task(kind='mechanism')
        done = self.result(task, self.specs(task), theses=[self.thesis()])
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertEqual(len(done['theses']), 1)
        self.assertEqual(done['calculations'], [])

    def test_insufficient_judgment_does_not_disguise_a_missing_core_requirement(self):
        task = self.task(kind='mechanism')
        done = self.result(task, self.specs(task)[:1], theses=[self.thesis('证据不足', ['effective_date'])])
        self.assertEqual(done['status'], '部分完成')
        self.assertEqual(done['theses'][0]['judgment'], '证据不足')
        self.assertIn('适用范围', ' '.join(done['gaps']))

    def test_report_independently_checks_thesis_coverage_not_only_answer_refs(self):
        task = self.task(kind='mechanism')
        done = self.result(task, self.specs(task), theses=[self.thesis()])
        done['theses'][0]['support'] = ['effective_date']
        with self.assertRaisesRegex(ContractError, '确定性判断'):
            compose(done)

    def test_question_without_extra_evidence_needs_keeps_its_natural_factual_path(self):
        task = self.task(questions=[{'id': 'q1', 'question': '政策何时生效？', 'kind': 'fact'}])
        spec = policy_fixture.AutonomousReasoningTests.policy_spec(task)
        done = self.result(task, [spec])
        self.assertEqual(done['status'], '已完成')
        self.assertEqual(done['questions'][0]['evidence_needs'], [])

    def test_app_and_skill_share_read_only_plan_requirements(self):
        task = self.task()
        contract = evidence_contract(task)
        self.assertEqual(contract['question_requirements']['q1'], evidence_requirements(task['questions'][0]))
        self.assertIn('evidence_need_ids', contract['review_fields'])
        before = fingerprint(task)
        spec = importlib.util.spec_from_file_location('coverage_skill', ROOT / 'core/session.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        session = module.Session.__new__(module.Session)
        session.archive = self.agent.archive
        self.assertEqual(session.tools(task['id'])['evidence'], contract)
        self.assertEqual(before, fingerprint(self.agent.archive.get(task['id'])))


if __name__ == '__main__':
    unittest.main()
