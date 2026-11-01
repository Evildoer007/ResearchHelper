"""Complete publication-reference grammar with unchanged provenance gates."""

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, DOCUMENT_CONTENT_INTENT, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import validate_publication_basis
from researchhelper.reasoning import evidence_contract, source_views
from tests import test_autonomous_reasoning_offline as reasoning_fixture
from tests import test_filing_workflow_offline as filing_fixture

ROOT = Path(__file__).resolve().parents[2]
TEXT = '材料示例政策\n\n发布日期：2026年7月19日。\n\n政策自2026年7月21日起生效。'
MATERIAL_FIELDS = ['kind', 'artifact_id', 'snapshot_hash', 'part', 'quote']
DIRECTORY_FIELDS = ['kind', 'artifact_id', 'snapshot_hash', 'field']


class PublicationReferenceTests(unittest.TestCase):
    def setUp(self):
        out = ROOT / 'result/acceptance/publication-reference'
        out.mkdir(parents=True, exist_ok=True)
        folder = tempfile.TemporaryDirectory(prefix='isolated-', dir=out)
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)
        network = patch('socket.socket.connect', side_effect=AssertionError('No external calls'))
        network.start()
        self.addCleanup(network.stop)

    def task(self, intent='综合研究'):
        task = self.agent.create('这份政策何时生效？', objects=['材料示例政策'], intent=intent,
            cutoff='2026-07-20T18:00:00+08:00')
        return self.agent.add_material(task['id'], 'policy.txt', TEXT.encode())

    def test_material_lists_all_required_keys_and_exact_fixed_values(self):
        task = self.task()
        before = fingerprint(task)
        contract = evidence_contract(task)
        source = contract['sources'][0]
        record = task['materials'][0]
        self.assertEqual(source['publication_required_fields'], MATERIAL_FIELDS)
        self.assertEqual(source['publication_fixed_fields'], {'kind': 'material', 'artifact_id': record['id'],
            'snapshot_hash': record['snapshot_hash']})
        self.assertEqual(source['publication_reading_fields'], ['part', 'quote'])
        self.assertEqual(contract['publication_reference_contract']['required_fields_by_kind']['material'], MATERIAL_FIELDS)
        self.assertFalse(contract['publication_reference_contract']['automatic_reference_repair'])
        self.assertFalse(contract['publication_reference_contract']['reference_is_approval'])
        self.assertEqual(fingerprint(task), before)

    def test_contract_mutation_does_not_mutate_task_or_other_schema(self):
        task = self.task()
        before = fingerprint(task)
        contract = evidence_contract(task)
        contract['sources'][0]['publication_fixed_fields']['snapshot_hash'] = 'changed'
        contract['sources'][0]['publication_required_fields'].clear()
        contract['publication_reference_contract']['required_fields_by_kind']['material'].clear()
        self.assertEqual(evidence_contract(task)['sources'][0]['publication_required_fields'], MATERIAL_FIELDS)
        self.assertEqual(fingerprint(task), before)

    def test_each_original_keeps_its_own_binding(self):
        task = self.agent.add_material(self.task()['id'], 'other.txt', TEXT.replace('2026年7月19日', '2026年7月18日').encode())
        sources = evidence_contract(task)['sources']
        self.assertNotEqual(sources[0]['publication_fixed_fields'], sources[1]['publication_fixed_fields'])
        for source in sources:
            for candidate in source['publication_candidates']:
                self.assertTrue(all(candidate['basis_ref'][key] == value
                    for key, value in source['publication_fixed_fields'].items()))

    def test_directory_reference_is_complete_and_requires_no_material_fields(self):
        task = self.agent.create('公告何时生效？', objects=['600000.SH'], cutoff='2026-10-02T18:00:00+08:00')
        directory = {'issuer': '600000.SH', 'source': 'ifind', 'source_id': '10001',
            'title': '合成政策公告', 'published_at': '2026-10-01T18:00:00+08:00',
            'original_url': 'https://static.cninfo.com.cn/finalpage/2026-10-01/10001.PDF',
            'query_coverage': {'codes': '600000.SH', 'begin': '2026-10-01', 'end': '2026-10-02', 'offline_fixture': True}}
        task = self.agent.add_filing(task['id'], directory, 'original.pdf', filing_fixture.policy_pdf())
        source = evidence_contract(task)['sources'][0]
        self.assertEqual(source['publication_required_fields'], DIRECTORY_FIELDS)
        self.assertEqual(source['publication_fixed_fields'], source['publication_reference'])
        self.assertEqual(source['publication_reading_fields'], [])
        spec = filing_fixture.FilingWorkflowTests.spec(None, task['source_records'][0])
        validate_publication_basis(spec, task['source_records'])

    def test_document_content_has_no_publication_reference_schema(self):
        task = self.task(DOCUMENT_CONTENT_INTENT)
        contract = evidence_contract(task)
        source = contract['sources'][0]
        self.assertIsNone(contract['publication_reference_contract'])
        self.assertIsNone(source['publication_reference'])
        self.assertNotIn('publication_fixed_fields', source)
        self.assertNotIn('publication_required_fields', source)
        self.assertEqual(source['publication_candidates'], [])
        self.assertIsNone(contract['document_publication_template']['basis_ref'])

    def test_api_observation_date_does_not_gain_publication_reference(self):
        task = self.agent.create('行情事实', objects=['600000.SH'], cutoff='2026-10-02T18:00:00+08:00')
        task = self.agent.add_snapshot(task['id'], 'ifind', 'cmd_history_quotation',
            {'codes': '600000.SH'}, {'errorcode': 0, 'tables': []})
        source = evidence_contract(task)['sources'][0]
        self.assertIsNone(source['publication_reference'])
        self.assertNotIn('publication_required_fields', source)
        self.assertNotIn('publication_fixed_fields', source)

    def test_missing_wrong_or_foreign_hash_still_rejects_without_repair(self):
        task = self.task()
        spec = reasoning_fixture.AutonomousReasoningTests.policy_spec(task)
        reference = spec['publication']['basis_ref']
        for value in (None, '', 'foreign-content'):
            changed = copy.deepcopy(spec)
            if value is None:
                changed['publication']['basis_ref'].pop('snapshot_hash')
            else:
                changed['publication']['basis_ref']['snapshot_hash'] = value
            before = fingerprint(changed)
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_publication_basis(changed, task['materials'])
            self.assertEqual(fingerprint(changed), before)
        self.assertEqual(reference['snapshot_hash'], task['materials'][0]['snapshot_hash'])
        validate_publication_basis(spec, task['materials'])

    def test_fixed_fields_do_not_approve_unknown_or_future_publication(self):
        task = self.task()
        spec = reasoning_fixture.AutonomousReasoningTests.policy_spec(task)
        source = evidence_contract(task)['sources'][0]
        spec['publication']['basis_ref'] = dict(source['publication_fixed_fields'], part='段落3',
            quote='政策自2026年7月21日起生效。')
        with self.assertRaises(ContractError):
            validate_publication_basis(spec, task['materials'])

    def test_app_extract_and_skill_receive_identical_complete_grammar(self):
        task = self.task()
        class Host:
            def reason(self, task, messages):
                self.payload = json.loads(messages[1]['content'])
                return '{"evidence_specs":[]}'
        host = Host()
        self.agent.model = host
        task.update(host_result={}, status='运行中')
        self.agent.archive.save(task)
        self.agent._extract_model_sources(task)
        contract = evidence_contract(task)
        self.assertEqual(host.payload['evidence_contract'], contract)
        spec = importlib.util.spec_from_file_location('reference_skill', ROOT / 'core/session.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        session = module.Session.__new__(module.Session)
        session.archive = self.agent.archive
        self.assertEqual(session.tools(task['id'])['evidence'], contract)

    def test_unread_or_changed_view_cannot_supply_a_reference(self):
        task = self.task()
        views = copy.deepcopy(source_views(task))
        views[0]['parts'][1]['text'] = '发布日期：2026年7月18日。'
        with self.assertRaises(ContractError):
            evidence_contract(task, views)


if __name__ == '__main__':
    unittest.main()
