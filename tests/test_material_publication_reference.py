"""Explicit short references resolve only to current, actually read originals.

These independent regressions never contact a model or a supplier. Publication
meaning, cutoff, original quotes and financial review remain separate gates.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, DOCUMENT_CONTENT_INTENT, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import validate_publication_basis
from researchhelper import reasoning
from researchhelper.source_reader import binding, read, retained_reading, validate_read_specs

ROOT = Path(__file__).resolve().parents[1]
TEXT = '材料示例政策\n\n发布日期：2026年7月19日。\n\n政策自2026年7月21日起生效。'


def policy_spec(task):
    original = task['materials'][0]
    return {'id': 'effective_date', 'artifact_id': original['id'], 'object': '材料示例政策',
        'record_locator': {'part': '段落3'}, 'text': '政策自2026年7月21日起生效。',
        'period': '政策条款', 'nature': '合同政策条款', 'unit': '不适用', 'question_ids': ['q1'],
        'published_at': '2026-07-19', 'publication': {'at': '2026-07-19', 'precision': '日',
            'timezone': 'Asia/Shanghai', 'certainty': '已确认', 'basis': '原件记载的发布日期',
            'basis_ref': {'artifact_id': original['id'], 'snapshot_hash': original['snapshot_hash'],
                'part': '段落2', 'quote': '发布日期：2026年7月19日。'}},
        'review': {'question_ids': ['q1'], 'allowed_natures': ['合同政策条款'],
            'conditions': ['仅核对公布条款，不判断未来经营结果'], 'reason': '发布日期与生效日期分别记录'}}


def answer():
    return {'answers': {'q1': {'answer': '原件所载政策自2026年7月21日起生效。',
        'refs': ['effective_date'], 'calculation_refs': []}}, 'theses': []}


class MaterialPublicationReferenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='material-reference-', dir=ROOT / 'result/acceptance')
        self.addCleanup(directory.cleanup)
        self.agent = ResearchAgent(ResearchArchive(directory.name), ROOT, offline=True)
        network = patch('socket.socket.connect', side_effect=AssertionError('No external calls'))
        network.start()
        self.addCleanup(network.stop)
        task = self.agent.create('这份政策何时生效？', objects=['材料示例政策'], cutoff='2026-07-20T18:00:00+08:00',
            questions=[{'id': 'q1', 'question': '这份政策何时生效？', 'kind': 'fact'}])
        self.task = self.agent.add_material(task['id'], 'policy.txt', TEXT.encode())

    def reference(self, task=None, views=None):
        source = reasoning.evidence_contract(task or self.task, views)['sources'][0]
        return source['material_publication_references'][0]['candidate_id']

    def spec(self, task=None, views=None):
        task = task or self.task
        spec = policy_spec(task)
        spec['publication'].pop('basis_ref')
        spec['material_publication_candidate_id'] = self.reference(task, views)
        return spec

    def expand(self, spec=None, task=None, views=None):
        return reasoning.expand_material_publication_references(task or self.task, [spec or self.spec()], views)[0]

    def test_exact_expansion_preserves_input_and_never_supplies_a_date(self):
        spec = self.spec()
        before = fingerprint((self.task, spec))
        expanded = self.expand(spec)
        expected = policy_spec(self.task)
        expected['publication']['basis_ref']['kind'] = 'material'
        self.assertEqual(expanded, expected)
        self.assertEqual(fingerprint((self.task, spec)), before)
        self.assertFalse(self.task['evidence'])
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)

    def test_legacy_candidate_shape_and_reference_format_remain_unchanged(self):
        contract = reasoning.evidence_contract(self.task)
        candidate = contract['sources'][0]['publication_candidates'][0]
        self.assertEqual(set(candidate), {'basis_ref', 'reading_range'})
        legacy = policy_spec(self.task)
        self.assertEqual(self.expand(legacy), legacy)

    def test_stable_short_reference_is_not_financial_approval(self):
        contract = reasoning.evidence_contract(self.task)
        self.assertEqual(self.reference(), self.reference())
        self.assertRegex(self.reference(), r'^rh_pub_[a-f0-9]{24}$')
        self.assertFalse(contract['compact_material_publication']['reference_is_approval'])
        self.assertFalse(contract['compact_material_publication']['automatic_timestamp_inference'])

    def test_unknown_reference_is_rejected(self):
        spec = self.spec()
        spec['material_publication_candidate_id'] = 'rh_pub_unknown'
        with self.assertRaises(ContractError):
            self.expand(spec)

    def test_empty_nonstring_reference_is_rejected(self):
        for value in ('', None, 3, True, {}):
            spec = self.spec()
            spec['material_publication_candidate_id'] = value
            with self.subTest(value=value), self.assertRaises(ContractError):
                self.expand(spec)

    def test_wrong_evidence_original_is_rejected(self):
        spec = self.spec()
        spec['artifact_id'] = 'other-original'
        with self.assertRaises(ContractError):
            self.expand(spec)

    def test_dual_reference_format_is_rejected_even_for_null_basis(self):
        for value in (None, {}, policy_spec(self.task)['publication']['basis_ref']):
            spec = self.spec()
            spec['publication']['basis_ref'] = value
            with self.subTest(value=value), self.assertRaises(ContractError):
                self.expand(spec)

    def test_reference_does_not_create_missing_publication_fields(self):
        spec = self.spec()
        spec['publication'].pop('certainty')
        expanded = self.expand(spec)
        self.assertNotIn('certainty', expanded['publication'])
        final = self.agent.start(self.task['id'], {'evidence_specs': [expanded],
            **answer()})
        self.assertFalse(final['evidence'])
        self.assertEqual(final['status'], '部分完成')

    def test_changed_date_is_not_repaired_or_approved(self):
        spec = self.spec()
        spec['published_at'] = spec['publication']['at'] = '2026-07-18'
        expanded = self.expand(spec)
        self.assertEqual(expanded['published_at'], '2026-07-18')
        with self.assertRaises(ContractError):
            validate_publication_basis(expanded, self.task['materials'])

    def test_task_version_input_and_cutoff_invalidate_old_reference(self):
        spec = self.spec()
        for field, value in (('id', 'foreign-task'), ('version', 2), ('input_hash', 'changed'),
                             ('cutoff', '2026-07-21T18:00:00+08:00')):
            task = copy.deepcopy(self.task)
            task[field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                self.expand(spec, task)

    def test_snapshot_changes_invalidate_old_reference(self):
        task = copy.deepcopy(self.task)
        task['materials'][0]['snapshot']['parts'][1]['text'] = '发布日期：2026年7月18日。'
        task['materials'][0]['snapshot_hash'] = fingerprint(task['materials'][0]['snapshot'])
        with self.assertRaises(ContractError):
            self.expand(self.spec(), task)

    def test_forged_reading_does_not_generate_references(self):
        views = reasoning.source_views(self.task)
        views[0]['parts'][1]['text'] = '发布日期：2026年7月18日。'
        with self.assertRaises(ContractError):
            self.reference(views=views)

    def test_no_read_publication_cue_cannot_use_full_original_reference(self):
        views = reasoning.source_views(self.task)
        views[0]['parts'] = [views[0]['parts'][2]]
        with self.assertRaises(ContractError):
            self.expand(self.spec(), views=views)

    def test_clipped_reference_binds_exact_exposed_span(self):
        task = self.agent.add_material(self.task['id'], 'long.txt',
            ('说明。' * 100 + '发布日期：2026年7月19日。' + '附注。' * 100).encode())
        views = reasoning.source_views(task)[1:]
        original = views[0]['parts'][0]['text']
        start = original.index('发布日期：')
        end = start + len('发布日期：2026年7月19日。')
        views[0]['parts'] = [dict(locator='段落1', text=original[start:end], reading_start=start,
                                 reading_end=end, original_characters=len(original))]
        source = reasoning.evidence_contract(task, views)['sources'][0]
        spec = self.spec()
        spec.update(artifact_id=views[0]['id'], material_publication_candidate_id=source['material_publication_references'][0]['candidate_id'])
        self.assertEqual(self.expand(spec, task, views)['publication']['basis_ref']['quote'], original[start:end])
        with self.assertRaises(ContractError):
            self.expand(spec, task)

    def test_saved_reading_and_host_preflight_use_same_reference(self):
        record = self.task['materials'][0]
        bundle = read(self.task, [dict(artifact_id=record['id'], snapshot_hash=record['snapshot_hash'],
            queries=['生效'], parts=[], reason='核对条款及发布时间', question_ids=['q1'])], binding(self.task))
        self.task['source_readings'] = [bundle]
        self.assertEqual(retained_reading(self.task, bundle['reading_hash']), bundle)
        spec = self.spec(views=bundle['views'])
        host = self.agent._validate_host_input(self.task, {'evidence_specs': [spec], 'reading_hash': bundle['reading_hash']})
        validate_read_specs(self.task, host['evidence_specs'], bundle)
        self.assertNotIn('material_publication_candidate_id', host['evidence_specs'][0])

    def test_unknown_or_tampered_saved_reading_is_rejected_before_run_state(self):
        before = fingerprint(self.task)
        with self.assertRaises(ContractError):
            self.agent._validate_host_input(self.task, {'evidence_specs': [self.spec()], 'reading_hash': 'not-saved'})
        self.assertEqual(fingerprint(self.task), before)

    def test_app_extraction_decodes_explicit_wire_and_records_codec(self):
        wire = {'evidence_specs': [self.spec()]}
        class Model:
            def reason(inner, task, messages):
                inner.payload = json.loads(messages[1]['content'])
                return json.dumps(wire, ensure_ascii=False)
        model = Model()
        output, receipt = reasoning.model_object(model, self.task, 'extract',
            {'untrusted_sources': reasoning.source_views(self.task), 'evidence_contract': reasoning.evidence_contract(self.task)},
            {'evidence_specs'})
        self.assertEqual(output['evidence_specs'][0], self.expand())
        self.assertEqual(receipt['material_publication_wire_hash'], fingerprint(wire))
        self.assertEqual(receipt['material_publication_reference_version'], 1)

    def test_app_never_trusts_forged_contract_reference(self):
        spec = self.spec()
        spec['material_publication_candidate_id'] = 'forged'
        class Model:
            def reason(inner, task, messages):
                return json.dumps({'evidence_specs': [spec]})
        contract = reasoning.evidence_contract(self.task)
        contract['sources'][0]['material_publication_references'][0]['candidate_id'] = 'forged'
        with self.assertRaises(ContractError):
            reasoning.model_object(Model(), self.task, 'extract',
                {'untrusted_sources': reasoning.source_views(self.task), 'evidence_contract': contract}, {'evidence_specs'})

    def test_legacy_bad_reference_is_not_auto_repaired(self):
        spec = policy_spec(self.task)
        spec['publication']['basis_ref']['artifact_id'] += self.task['materials'][0]['snapshot_hash']
        spec['publication']['basis_ref'].pop('snapshot_hash')
        self.assertEqual(self.expand(spec), spec)
        with self.assertRaises(ContractError):
            validate_publication_basis(spec, self.task['materials'])

    def test_document_content_mode_does_not_offer_or_accept_references(self):
        task = copy.deepcopy(self.task)
        task['intent'] = DOCUMENT_CONTENT_INTENT
        self.assertFalse(reasoning.evidence_contract(task)['sources'][0]['material_publication_references'])
        with self.assertRaises(ContractError):
            self.expand(self.spec(), task)

    def test_host_delivers_report_with_expanded_reference_and_no_model(self):
        final = self.agent.start(self.task['id'], {'evidence_specs': [self.spec()],
            **answer()})
        self.assertEqual(final['status'], '已完成', final.get('error'))
        self.assertTrue(final['delivery'])
        self.assertEqual(final['evidence'][0]['publication']['basis_ref']['snapshot_hash'], self.task['materials'][0]['snapshot_hash'])
        self.assertEqual(final['ledger']['used']['model_calls'], 0)


if __name__ == '__main__':
    unittest.main()
