"""Publication dates must belong to the cited release, not a neighboring date.

All originals are explicit synthetic materials. No supplier or model is called.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, DOCUMENT_CONTENT_INTENT, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import validate_publication_basis
from researchhelper.reasoning import evidence_contract, validate_model_specs
from tests import test_autonomous_reasoning_offline as reasoning_fixture

ROOT = Path(__file__).resolve().parents[2]


class PublicationQuoteBindingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='publication-date-binding-', dir=ROOT / 'result/acceptance')
        self.addCleanup(directory.cleanup)
        self.agent = ResearchAgent(ResearchArchive(directory.name), ROOT, offline=True)
        network = patch('socket.socket.connect', side_effect=AssertionError('External requests forbidden'))
        network.start()
        self.addCleanup(network.stop)

    def prepare(self, release, *, published='2026-07-19', quote=None, intent='综合研究'):
        task = self.agent.create('这份政策什么时候生效？', objects=['材料示例政策'], intent=intent,
            cutoff='2026-07-20T18:00:00+08:00',
            questions=[{'id': 'q1', 'question': '这份政策什么时候生效？', 'kind': 'fact'}])
        text = '材料示例政策，合成验收材料。\n\n' + release + '\n\n政策自2026年7月21日起生效。'
        task = self.agent.add_material(task['id'], 'synthetic-policy.txt', text.encode())
        spec = reasoning_fixture.AutonomousReasoningTests.policy_spec(task)
        spec['published_at'] = spec['publication']['at'] = published
        spec['publication']['basis_ref']['quote'] = quote or release
        return task, spec

    def test_report_period_cannot_be_taken_from_a_later_release_sentence(self):
        task, spec = self.prepare('本文件于2026年7月25日公布，报告期截至2026年7月19日。')
        before = fingerprint(spec)
        with self.assertRaises(ContractError):
            validate_publication_basis(spec, task['materials'])
        self.assertEqual(fingerprint(spec), before)

    def test_effective_date_is_not_publication_even_in_the_same_quote(self):
        task, spec = self.prepare('发布日期：2026年7月25日；政策自2026年7月19日起生效。')
        with self.assertRaises(ContractError):
            validate_model_specs(task, [spec])

    def test_single_reporting_date_does_not_gain_publication_meaning_from_an_earlier_word(self):
        task, spec = self.prepare('已披露文件的报告期截至2026年7月19日，实际发布日期为2026年7月25日。',
            quote='已披露文件的报告期截至2026年7月19日')
        with self.assertRaises(ContractError):
            validate_publication_basis(spec, task['materials'])

    def test_explicit_release_can_be_selected_without_the_other_date(self):
        task, spec = self.prepare('发布日期：2026年7月19日；报告期截至2026年7月18日。',
            quote='发布日期：2026年7月19日')
        validate_model_specs(task, [spec])
        host = {'evidence_specs': [spec], **reasoning_fixture.AutonomousReasoningTests.answer()}
        final = self.agent.start(task['id'], host)
        self.assertEqual(final['status'], '已完成')
        self.assertEqual(len(final['evidence']), 1)
        self.assertEqual(final['ledger']['used']['model_calls'], 0)
        self.assertEqual(final['ledger']['used']['data_requests'], 0)

    def test_date_before_release_title_and_repeated_same_day_are_supported(self):
        for release in ('2026年7月19日材料示例政策受权公布公告',
                        '发布日期：2026年7月19日，发布日2026-07-19。'):
            with self.subTest(release=release):
                task, spec = self.prepare(release)
                validate_publication_basis(spec, task['materials'])

    def test_correct_release_in_complete_multi_date_quote_remains_usable(self):
        task, spec = self.prepare('报告期截至2026年7月18日。发布日期：2026年7月19日。政策自2026年7月21日生效。')
        validate_publication_basis(spec, task['materials'])
        final = self.agent.start(task['id'], {'evidence_specs': [spec], **reasoning_fixture.AutonomousReasoningTests.answer()})
        self.assertEqual(final['status'], '已完成')

    def test_planned_or_unpublished_cue_does_not_prove_publication(self):
        for release in ('计划发布：2026年7月19日。', '预计于2026年7月19日公布。',
                        '截至2026年7月19日尚未披露。'):
            with self.subTest(release=release):
                task, spec = self.prepare(release)
                with self.assertRaises(ContractError):
                    validate_publication_basis(spec, task['materials'])

    def test_cutting_off_the_plan_prefix_cannot_change_the_original_meaning(self):
        task, spec = self.prepare('计划发布：2026年7月19日。', quote='发布：2026年7月19日')
        with self.assertRaises(ContractError):
            validate_publication_basis(spec, task['materials'])

    def test_bad_publication_is_rejected_in_host_workflow_not_only_model_schema(self):
        task, spec = self.prepare('报告期截至2026年7月19日，本文件于2026年7月25日公布。')
        final = self.agent.start(task['id'], {'evidence_specs': [spec], **reasoning_fixture.AutonomousReasoningTests.answer()})
        self.assertEqual(final['status'], '部分完成')
        self.assertEqual(final['evidence'], [])
        self.assertTrue(final['rejected_evidence'])
        self.assertEqual(final['ledger']['used']['data_requests'], 0)

    def test_document_content_has_no_publication_claim_and_contract_is_read_only(self):
        task, spec = self.prepare('报告期截至2026年7月19日。计划发布：2026年7月25日。',
            intent=DOCUMENT_CONTENT_INTENT)
        spec.update(usage_scope='document_content', published_at=None)
        spec['publication'] = {'at': None, 'basis': '首次公开时间未核验', 'precision': '未知',
            'timezone': '未知', 'certainty': '未知', 'basis_ref': None}
        before = fingerprint(task)
        validate_model_specs(task, [spec])
        contract = evidence_contract(task)
        self.assertIsNone(contract['publication_reference_contract'])
        self.assertEqual(fingerprint(task), before)


if __name__ == '__main__':
    unittest.main()
