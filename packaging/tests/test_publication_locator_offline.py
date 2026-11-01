"""Source-bound reading aids, never automatic publication acceptance."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, DOCUMENT_CONTENT_INTENT, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import validate_publication_basis
from researchhelper.reasoning import evidence_contract, source_views
from researchhelper.source_reader import binding, read
from tests import test_autonomous_reasoning_offline as fixtures

ROOT = Path(__file__).resolve().parents[2]
TEXT = '材料示例政策\n\n发布日期：2026年7月19日。\n\n政策自2026年7月21日起生效。'


class PublicationLocatorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='publication-locator-', dir=ROOT / 'result/acceptance')
        self.addCleanup(directory.cleanup)
        self.agent = ResearchAgent(ResearchArchive(directory.name), ROOT, offline=True)
        network = patch('socket.socket.connect', side_effect=AssertionError('External network forbidden'))
        network.start()
        self.addCleanup(network.stop)

    def task(self, text=TEXT, **options):
        task = self.agent.create('这份政策何时生效？', objects=['材料示例政策'],
            cutoff='2026-07-20T18:00:00+08:00', **options)
        return self.agent.add_material(task['id'], 'policy.txt', text.encode())

    def candidates(self, task, views=None):
        return evidence_contract(task, views)['sources'][0]['publication_candidates']

    def test_exact_candidate_is_read_only_and_does_not_propose_timestamp(self):
        task = self.task()
        before = fingerprint(task)
        contract = evidence_contract(task)
        candidate = contract['sources'][0]['publication_candidates'][0]
        self.assertEqual(candidate['basis_ref']['quote'], '发布日期：2026年7月19日。')
        self.assertEqual(candidate['basis_ref']['part'], '段落2')
        self.assertEqual(candidate['basis_ref']['snapshot_hash'], task['materials'][0]['snapshot_hash'])
        self.assertEqual(candidate['reading_range'], {'start': 0, 'end': len(candidate['basis_ref']['quote'])})
        self.assertFalse(contract['publication_candidate_contract']['candidate_is_approval'])
        self.assertFalse(contract['publication_candidate_contract']['automatic_timestamp_inference'])
        self.assertNotIn('published_at', candidate)
        self.assertEqual(fingerprint(task), before)
        self.assertEqual(task['evidence'], [])

    def test_title_with_date_and_publication_cue_is_located(self):
        task = self.task('2026年7月19日材料示例政策受权公布公告\n\n2026-07-19 09:00\n\n政策自2026年7月21日起生效。')
        candidates = self.candidates(task)
        self.assertEqual(len(candidates), 1)
        self.assertIn('受权公布公告', candidates[0]['basis_ref']['quote'])
        self.assertEqual(candidates[0]['basis_ref']['part'], '段落1')

    def test_bare_minute_time_and_effective_date_are_not_publication_anchors(self):
        task = self.task('2026-07-19 09:00\n\n政策自2026年7月21日起生效。')
        self.assertEqual(self.candidates(task), [])

    def test_absence_of_candidate_is_not_proof_of_no_publication(self):
        contract = evidence_contract(self.task('原件没有给出公开时点。'))
        self.assertFalse(contract['publication_candidate_contract']['absence_is_proof'])
        self.assertEqual(contract['sources'][0]['publication_candidates'], [])

    def test_historical_or_future_cues_remain_unapproved_reading_candidates(self):
        contract = evidence_contract(self.task('历史披露：2020年1月1日旧政策。\n\n计划发布：2030年1月1日。'))
        candidates = contract['sources'][0]['publication_candidates']
        self.assertEqual(len(candidates), 2)
        self.assertIn('历史引用、未来计划', contract['publication_candidate_contract']['semantic_rule'])
        self.assertTrue(all(set(candidate) == {'basis_ref', 'reading_range'} for candidate in candidates))

    def test_document_only_never_acquires_publication_candidates_or_changes_intent(self):
        task = self.task(intent=DOCUMENT_CONTENT_INTENT)
        contract = evidence_contract(task)
        self.assertEqual(contract['sources'][0]['publication_candidates'], [])
        self.assertEqual(contract['document_publication_template']['certainty'], '未知')
        self.assertEqual(task['intent'], DOCUMENT_CONTENT_INTENT)

    def test_forged_view_text_is_rejected_even_with_original_snapshot_hash(self):
        task = self.task()
        views = copy.deepcopy(source_views(task))
        views[0]['parts'][1]['text'] = '发布日期：2026年7月18日。'
        with self.assertRaisesRegex(ContractError, '未读取或篡改'):
            evidence_contract(task, views)

    def test_foreign_snapshot_binding_rejects_before_candidate_search(self):
        task = self.task()
        views = copy.deepcopy(source_views(task))
        views[0]['snapshot_hash'] = 'different-content'
        with self.assertRaises(ContractError):
            evidence_contract(task, views)

    def test_only_actual_selected_span_is_searched_not_omitted_neighbors(self):
        text = '说明。' * 300 + '发布日期：2026年7月19日。' + '政策自2026年7月21日起生效。' * 300
        task = self.task(text)
        view = copy.deepcopy(source_views(task)[0])
        original = view['parts'][0]['text']
        view['parts'] = [{'locator': '段落1', 'text': original[:50],
            'reading_start': 0, 'reading_end': 50, 'original_characters': len(original)}]
        self.assertEqual(self.candidates(task, [view]), [])
        start = original.index('发布日期：')
        end = start + len('发布日期：2026年7月19日。')
        view['parts'] = [{'locator': '段落1', 'text': original[start:end],
            'reading_start': start, 'reading_end': end, 'original_characters': len(original)}]
        candidate = self.candidates(task, [view])[0]
        self.assertEqual(candidate['reading_range'], {'start': start, 'end': end})
        self.assertEqual(candidate['basis_ref']['quote'], original[start:end])

    def test_real_reader_views_have_compatible_candidate_ranges(self):
        task = self.task()
        material = task['materials'][0]
        bundle = read(task, [{'artifact_id': material['id'], 'snapshot_hash': material['snapshot_hash'],
            'queries': ['生效'], 'parts': [], 'reason': '核对条款与发布依据', 'question_ids': ['q1']}], binding(task))
        candidates = self.candidates(task, bundle['views'])
        self.assertEqual(candidates[0]['basis_ref']['part'], '段落2')

    def test_candidate_cap_does_not_skip_validation_of_later_spans(self):
        task = self.task('\n\n'.join('发布日期：2026年7月19日，第%d段。' % i for i in range(10)))
        self.assertEqual(len(self.candidates(task)), 4)
        views = copy.deepcopy(source_views(task))
        views[0]['parts'][-1]['text'] = '新增虚假发布依据'
        with self.assertRaises(ContractError):
            evidence_contract(task, views)

    def test_same_original_anchor_must_still_pass_existing_publication_validator(self):
        task = self.task()
        spec = fixtures.AutonomousReasoningTests.policy_spec(task)
        spec['publication']['basis_ref'] = self.candidates(task)[0]['basis_ref']
        validate_publication_basis(spec, task['materials'])
        spec['published_at'] = '2026-07-18'
        with self.assertRaisesRegex(ContractError, '不是所定位'):
            validate_publication_basis(spec, task['materials'])

    def test_bare_time_cannot_be_repaired_by_presence_of_a_valid_candidate(self):
        task = self.task(TEXT + '\n\n2026-07-19 09:00')
        spec = fixtures.AutonomousReasoningTests.policy_spec(task)
        spec['publication']['basis_ref'].update(part='段落4', quote='2026-07-19 09:00')
        before = fingerprint(spec)
        with self.assertRaisesRegex(ContractError, '未明确定位'):
            validate_publication_basis(spec, task['materials'])
        self.assertEqual(fingerprint(spec), before)

    def test_app_extract_receives_the_same_contract_without_generating_evidence(self):
        task = self.task()
        class Host:
            def reason(self, task, messages):
                self.payload = json.loads(messages[1]['content'])
                return '{"evidence_specs":[]}'
        host = Host()
        self.agent.model = host
        task['host_result'] = {}
        task['status'] = '运行中'
        self.agent.archive.save(task)
        result = self.agent._extract_model_sources(task)
        self.assertEqual(host.payload['evidence_contract'], evidence_contract(task))
        self.assertEqual(result['evidence_specs'], [])
        self.assertEqual(task['evidence'], [])

    def test_skill_tools_expose_identical_candidates_without_a_second_model(self):
        task = self.task()
        spec = importlib.util.spec_from_file_location('locator_skill', ROOT / 'core/session.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        session = module.Session.__new__(module.Session)
        session.archive = self.agent.archive
        self.assertEqual(session.tools(task['id'])['evidence'], evidence_contract(task))
        self.assertIsNone(self.agent.model)

    def test_object_contract_preserves_registered_names_and_does_not_invent_metadata_objects(self):
        task = self.task()
        before = fingerprint(task)
        contract = evidence_contract(task)['object_binding_contract']
        self.assertEqual(contract['allowed_values'], ['材料示例政策'])
        self.assertFalse(contract['allow_unlisted'])
        self.assertFalse(contract['automatic_object_repair'])
        self.assertEqual(fingerprint(task), before)

    def test_unlisted_metadata_object_is_rejected_without_relabeling_proposal(self):
        from researchhelper.reasoning import validate_model_specs
        task = self.task()
        spec = fixtures.AutonomousReasoningTests.policy_spec(task)
        spec['object'] = '材料示例政策发布日期'
        before = fingerprint(spec)
        with self.assertRaisesRegex(ContractError, '对象超出'):
            validate_model_specs(task, [spec])
        self.assertEqual(fingerprint(spec), before)

    def test_empty_object_scope_is_not_silently_populated_by_reading_contract(self):
        task = self.agent.create('这份材料写了什么？', cutoff='2026-07-20T18:00:00+08:00')
        task = self.agent.add_material(task['id'], 'policy.txt', TEXT.encode())
        before = fingerprint(task)
        contract = evidence_contract(task)['object_binding_contract']
        self.assertEqual(contract['allowed_values'], [])
        self.assertTrue(contract['allow_unlisted'])
        self.assertEqual(fingerprint(task), before)


if __name__ == '__main__':
    unittest.main()
