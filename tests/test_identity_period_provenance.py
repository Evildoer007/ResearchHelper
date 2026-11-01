"""Independent source-proof tests, with an explicitly synthetic mapping witness.

Retained real price bytes are read-only. The fixture witness is not claimed
as a real historical identity source and is never added to the original task.
"""
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if importlib.util.find_spec('researchhelper') is None:
    from core.bootstrap import configure_imports
    configure_imports()
from researchhelper.contracts import ContractError
from researchhelper.evidence import (candidate_evidence, extract_material, material_record,
    evidence_binding, validate_identity_period_basis, identity_period_basis_contract,
    identity_period_statement, validate_provenance)
from researchhelper.reasoning import MODEL_EVIDENCE_FIELDS, evidence_contract, RULES_VERSION
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent


class IdentityPeriodProvenanceTests(unittest.TestCase):
    def setUp(self):
        original = ROOT / 'result/acceptance/actual-native-market-report-xe9qfdv7'
        receipt = json.loads((original / 'receipt.json').read_text())
        with sqlite3.connect((original / 'store/researchhelper.sqlite3').as_uri() + '?mode=ro', uri=True) as connection:
            body = connection.execute('select body from tasks where id=?', (receipt['task_id'],)).fetchone()[0]
        self.task = json.loads(body)
        retained = self.task['evidence'][0]
        self.artifact = next(item for item in self.task['source_records'] if item['id'] == retained['artifact_id'])
        self.spec = {key: copy.deepcopy(value) for key, value in retained.items() if key in MODEL_EVIDENCE_FIELDS}
        self.statement = ('供应商：ifind；供应商代码：510300.SH；研究对象：510300.SH；'
            '映射有效期起点：2026-09-21；映射有效期终点：2026-09-30')

    def witness(self, statement=None, prefix=''):
        statement = statement or self.statement
        published = '发布日期：2026年9月20日。'
        payload = (published + '\n\n' + prefix + statement).encode()
        record = material_record(self.task, extract_material('synthetic-identity-witness.txt', payload), payload)
        self.task['materials'].append(record)
        parts = record['snapshot']['parts']
        source = parts[1]['locator']
        question = self.task['questions'][0]['id']
        spec = dict(id='synthetic_identity_witness', artifact_id=record['id'], object='510300.SH',
            record_locator={'part': source}, text=statement, published_at='2026-09-20',
            publication={'at': '2026-09-20', 'basis': '测试原件明确公布日', 'precision': '日',
                'timezone': 'Asia/Shanghai', 'certainty': '已确认',
                'basis_ref': {'kind': 'material', 'artifact_id': record['id'],
                    'snapshot_hash': record['snapshot_hash'], 'part': parts[0]['locator'], 'quote': published}},
            period='2026年9月', nature='已实现事实', unit='不适用', question_ids=[question])
        spec['review'] = {'question_ids': [question],
            'allowed_natures': ['已实现事实'], 'conditions': ['合成原件仅核对指定代码和映射期间，不证明价格或投资价值'],
            'reason': '独立测试的原件事实核对'}
        self.witness_spec = copy.deepcopy(spec)
        proof = candidate_evidence(self.task, record, spec)
        proof = ResearchAnalyzer().assess(self.task, proof, spec['review'], self.task['materials'] + self.task['source_records'])
        self.task['evidence'].append(proof)
        self.spec['identity'].update(basis=statement, basis_ref={'version': 1,
            'evidence_id': proof['id'], 'evidence_hash': evidence_binding(proof), 'content_version': proof['content_version']})
        return proof

    def candidate(self):
        return candidate_evidence(self.task, self.artifact, self.spec)

    def test_valid_source_witness_admits_original_price_and_new_receipt(self):
        self.witness()
        checked = self.candidate()
        self.assertEqual(checked['raw_value'], self.spec['raw_value'])
        self.assertEqual(checked['source_check'], '通过')
        self.assertEqual(checked['applicability_check'], '待核验')
        self.assertEqual(checked['source_receipt']['version'], 2)

    def test_no_reference_cannot_be_repaired_by_free_text_or_arbitrary_dates(self):
        self.spec['identity'].update(basis='无来源的猜测', valid_from='1900-01-01', valid_to='2999-12-31')
        with self.assertRaisesRegex(ContractError, '同版来源引用'):
            self.candidate()

    def test_contract_provides_exact_reference_without_mutation(self):
        self.witness()
        before = copy.deepcopy(self.task)
        contract = evidence_contract(self.task)['identity_period_provenance']
        self.assertEqual(contract['admissible_bases'][0]['identity'], self.spec['identity'])
        self.assertEqual(self.task, before)
        self.assertEqual(RULES_VERSION, 39)

    def test_cross_task_cross_version_pending_or_nonfact_witness_rejected(self):
        self.witness()
        for field, value in [('task_id', 'another-task'), ('research_version', 999),
            ('source_check', '待核验'), ('applicability_check', '待核验'), ('nature', '管理层展望')]:
            with self.subTest(field=field):
                task = copy.deepcopy(self.task)
                task['evidence'][-1][field] = value
                with self.assertRaises(ContractError):
                    candidate_evidence(task, self.artifact, self.spec)

    def test_content_reference_hash_version_and_basis_claim_are_bound(self):
        self.witness()
        for field, value in [('evidence_hash', '0' * 64), ('content_version', 999), ('version', True)]:
            with self.subTest(field=field):
                spec = copy.deepcopy(self.spec)
                spec['identity']['basis_ref'][field] = value
                with self.assertRaises(ContractError):
                    candidate_evidence(self.task, self.artifact, spec)
        self.spec['identity']['basis'] = '依据已由官方全部确认'
        with self.assertRaisesRegex(ContractError, '原文不一致'):
            self.candidate()

    def test_duplicate_witness_is_not_unique(self):
        self.witness()
        self.task['evidence'].append(copy.deepcopy(self.task['evidence'][-1]))
        with self.assertRaisesRegex(ContractError, '唯一登记'):
            self.candidate()

    def test_api_quote_is_not_its_own_period_proof(self):
        self.spec['identity']['basis_ref'] = {'version': 1, 'evidence_id': self.task['evidence'][0]['id'],
            'evidence_hash': evidence_binding(self.task['evidence'][0]), 'content_version': 1}
        with self.assertRaisesRegex(ContractError, '原件事实'):
            self.candidate()

    def test_altered_original_and_fake_success_receipts_are_rejected(self):
        self.witness()
        self.task['materials'][-1]['snapshot']['original_base64'] = 'ZmFrZQ=='
        with self.assertRaises(ContractError):
            self.candidate()

    def test_declared_period_must_equal_source_and_cover_observation(self):
        self.witness()
        self.spec['identity']['valid_to'] = '2999-12-31'
        with self.assertRaisesRegex(ContractError, '原文不一致'):
            self.candidate()
        self.spec['identity']['valid_to'] = '2026-09-20'
        with self.assertRaises(ContractError):
            self.candidate()

    def test_provider_conflict_is_not_silently_reconciled(self):
        self.witness(self.statement.replace('供应商：ifind', '供应商：tinyshare'))
        with self.assertRaisesRegex(ContractError, '供应商'):
            self.candidate()

    def test_unregistered_or_qualified_text_is_not_parsed_as_identity_period(self):
        for text in (self.statement.replace('映射有效期起点', '上市日期'),
                     '预计' + self.statement, '不是' + self.statement,
                     self.statement + '，仅供猜测', self.statement.replace('2026-09-30', '2026-09-20')):
            with self.subTest(text=text):
                with self.assertRaises(ContractError):
                    identity_period_statement(text)

    def test_contract_has_no_candidate_from_unverified_or_legacy_price_flags(self):
        self.assertEqual(identity_period_basis_contract(self.task)['admissible_bases'], [])

    def test_invalid_basis_is_rechecked_after_source_admission(self):
        self.witness()
        checked = self.candidate()
        self.task['evidence'][-1]['applicability_check'] = '待核验'
        with self.assertRaises(ContractError):
            validate_provenance(checked, self.task)

    def test_cannot_crop_negation_or_qualification_out_of_source_paragraph(self):
        self.witness(prefix='以下为未经确认的假设：')
        with self.assertRaisesRegex(ContractError, '不能裁剪'):
            self.candidate()

    def test_provider_brand_case_does_not_rewrite_original_quote(self):
        self.witness(self.statement.replace('供应商：ifind', '供应商：iFinD'))
        checked = self.candidate()
        self.assertIn('供应商：iFinD', checked['identity']['basis'])

    def test_explicit_selection_never_mixes_with_free_identity_claims(self):
        self.witness()
        self.spec['identity_basis_reference'] = {'version': 1, 'evidence_id': self.task['evidence'][-1]['id']}
        with self.assertRaisesRegex(ContractError, '混用'):
            self.candidate()
        self.spec.pop('identity')
        self.assertEqual(self.candidate()['source_check'], '通过')

    def test_same_batch_material_first_without_extra_calls_and_persists(self):
        self.witness()
        price = copy.deepcopy(self.spec)
        price['id'] = 'source_bound_price'
        price.pop('identity')
        price['identity_basis_reference'] = {'version': 1, 'evidence_id': self.witness_spec['id']}
        price['review'] = copy.deepcopy(self.task['evidence'][0]['applicability_review'])
        self.task['run_mode'] = 'host'
        ledger = copy.deepcopy(self.task['ledger'])
        with tempfile.TemporaryDirectory() as directory:
            archive = ResearchArchive(directory)
            agent = ResearchAgent(archive, ROOT, offline=True)
            # A controlled in-memory source-step replay, not a reopened live
            # task. API comes first deliberately; its proof is in this batch.
            agent._step(self.task, 'evidence', {'evidence_specs': [price, self.witness_spec]})
            self.assertEqual([item['id'] for item in self.task['evidence']],
                             [self.witness_spec['id'], price['id']])
            self.assertEqual(self.task['rejected_evidence'], [])
            self.assertEqual(self.task['ledger'], ledger)
            self.task['revision'] = 0
            archive.save(self.task)
            restored = ResearchArchive(directory).get(self.task['id'])
            validate_provenance(restored['evidence'][-1], restored)

    def test_same_batch_failed_material_does_not_authorize_api(self):
        self.witness()
        price = copy.deepcopy(self.spec)
        price.pop('identity')
        price['identity_basis_reference'] = {'version': 1, 'evidence_id': self.witness_spec['id']}
        price['review'] = copy.deepcopy(self.task['evidence'][0]['applicability_review'])
        invalid = copy.deepcopy(self.witness_spec)
        invalid['text'] = '原件没有这段文字'
        self.task['run_mode'] = 'host'
        with tempfile.TemporaryDirectory() as directory:
            ResearchAgent(ResearchArchive(directory), ROOT, offline=True)._step(self.task, 'evidence',
                {'evidence_specs': [price, invalid]})
        self.assertEqual(self.task['evidence'], [])
        self.assertEqual(len(self.task['rejected_evidence']), 2)

    def test_old_frozen_report_remains_readable_without_new_approval(self):
        original = ROOT / 'result/acceptance/actual-native-market-report-xe9qfdv7'
        database = original / 'store/researchhelper.sqlite3'
        archive = ResearchArchive.__new__(ResearchArchive)
        archive.connect = lambda: sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
        report = archive.report(self.task['id'], self.task['version'])
        self.assertEqual(report['html'], (original / 'live-report.html').read_text())
        self.assertEqual(report['content']['evidence'][0]['source_receipt']['version'], 1)
        with self.assertRaises(ContractError):
            validate_provenance(report['content']['evidence'][0], self.task)


if __name__ == '__main__':
    unittest.main()
