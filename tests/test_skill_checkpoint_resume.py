"""Retained host inputs survive checkpoint-only resume; no provider calls."""
import copy
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from core.session import Session
from researchhelper.contracts import ContractError
from tests.test_argument_support_workflow import assessed
from tests import test_autonomous_reasoning_offline as reasoning

ROOT = Path(__file__).resolve().parents[1]


class SkillCheckpointResumeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='researchhelper-skill-resume-')
        self.addCleanup(folder.cleanup)
        self.session = Session(folder.name)
        self.addCleanup(self.session.agent.close_runtime)
        network = patch.object(socket.socket, 'connect', side_effect=AssertionError('No external calls'))
        network.start()
        self.addCleanup(network.stop)

    def checkpoint(self):
        question = '这份政策什么时候生效？'
        task = self.session.create(question, objects=['材料示例政策'], cutoff=reasoning.CUTOFF,
            questions=[dict(id='q1', question=question, kind='fact')])
        text = ('材料示例政策\n\n发布日期：2026年7月19日。\n\n'
                '政策自2026年7月21日起生效。\n\n适用对象为符合条件的企业。')
        self.session.material(task['id'], 'policy.txt', text.encode())
        task = self.session.status(task['id'])
        outputs = reasoning.AutonomousReasoningTests.answer()
        prepared = self.session.run(task['id'], evidence_specs=[reasoning.AutonomousReasoningTests.policy_spec(task)],
            **outputs, defer_delivery=True)
        self.assertEqual(prepared['status'], '待开始')
        self.assertTrue(prepared['host_checkpoint'])
        wire, _ = assessed(prepared, prepared['host_result'])
        self.session.review_arguments(task['id'], wire)
        return self.session.status(task['id'])

    def assert_retained(self, before, after):
        self.assertEqual(after['version'], before['version'])
        self.assertEqual(after['cutoff'], before['cutoff'])
        self.assertEqual(after['host_result'], before['host_result'])
        self.assertEqual(after['ledger']['supplements'], before['ledger']['supplements'])
        for field in ('model_calls', 'data_requests'):
            self.assertEqual(after['ledger']['used'][field], before['ledger']['used'][field])
        self.assertEqual(after['ledger']['limits'], before['ledger']['limits'])
        self.assertEqual(after['evidence'], before['evidence'])
        self.assertEqual(after['questions'][0]['status'], '已回答')

    def test_checkpoint_resume_adopts_review_without_an_empty_submission_or_extra_round(self):
        before = self.checkpoint()
        for _ in range(3):
            resumed = self.session.resume(before['id'], defer_delivery=True)
            self.assert_retained(before, resumed)
            self.assertEqual(resumed['status'], '待开始')
            self.assertTrue(resumed['host_checkpoint'])
            self.assertIsNone(resumed['delivery'])
        final = self.session.deliver(before['id'])
        self.assertEqual(final['status'], '已完成', final['gaps'])
        self.assert_retained(before, final)
        report = self.session.archive.report(final['id'], final['version'])
        self.assertTrue(report['html_integrity_verified'])
        self.assertIn('2026年7月21日', report['html'])

    def test_default_resume_delivers_the_existing_reviewed_input(self):
        before = self.checkpoint()
        final = self.session.resume(before['id'])
        self.assertEqual(final['status'], '已完成', final['gaps'])
        self.assert_retained(before, final)
        frozen = self.session.archive.report(final['id'], final['version'])
        with self.assertRaisesRegex(ContractError, '任务不能重复启动'):
            self.session.resume(final['id'], defer_delivery=True)
        self.assertEqual(self.session.archive.report(final['id'], final['version']), frozen)

    def test_invalid_delivery_options_do_not_replace_inputs_or_consume_rounds(self):
        before = self.checkpoint()
        for value in (None, 0, 1, '', {}, []):
            with self.subTest(value=value), self.assertRaises(ContractError):
                self.session.resume(before['id'], defer_delivery=value)
            self.assertEqual(self.session.status(before['id']), before)

    def test_an_explicit_empty_answer_submission_keeps_its_distinct_meaning(self):
        before = self.checkpoint()
        updated = self.session.resume(before['id'], answers={}, defer_delivery=True)
        self.assertEqual(updated['host_result']['answers'], {})
        self.assertEqual(updated['ledger']['supplements'], before['ledger']['supplements'] + 1)
        self.assertEqual(updated['ledger']['used']['model_calls'], 0)
        self.assertEqual(updated['ledger']['used']['data_requests'], 0)
