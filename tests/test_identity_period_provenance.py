"""Synthetic quote and mapping originals for independent identity-period checks."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if importlib.util.find_spec('researchhelper') is None:
    from core.bootstrap import configure_imports
    configure_imports()
from researchhelper.contracts import ContractError, canonical, charge, fingerprint
from researchhelper.evidence import (
    _record_current_acquisition, candidate_evidence, current_snapshot_reference, direct_code_reference,
    evidence_binding, extract_material, identity_period_basis_contract, identity_period_statement,
    material_record, snapshot_record, validate_provenance, verification_receipt,
)
from researchhelper.reasoning import evidence_contract
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent


CODE = '510300.SH'
SESSION = '2026-09-30'
NOW = '2026-10-01T18:00:00+08:00'
PRICE_NEED = '核对交易日收盘原值'
IDENTITY_NEED = '核对证券标识的适用期间'


def review_for(need):
    return dict(question_ids=['q1'], allowed_natures=['已实现事实'], conditions=['仅用于合成取证验收'],
                reason='原件、主体及问题范围一致', evidence_need_ids={'q1': [fingerprint(need)]})


def quote_task(agent):
    """Use the normal acquisition and source checks with fabricated transport data."""
    with patch('researchhelper.engine.now', return_value=NOW), patch('researchhelper.evidence.now', return_value=NOW):
        task = agent.create('核对510300.SH合成行情及身份期间', entry='ETF', objects=[CODE],
                            research_mode='current', questions=[dict(id='q1', question='核对合成行情及身份期间',
                                kind='fact', evidence_needs=[PRICE_NEED, IDENTITY_NEED])])
        parameters = dict(codes=CODE, indicators='close', startdate=SESSION, enddate=SESSION,
                          functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
        record = snapshot_record(task, 'ifind', 'cmd_history_quotation', parameters,
                                 dict(errorcode=0, tables=[dict(thscode=CODE, time=[SESSION], table=dict(close=[4.5]))]))
    charge(task['ledger'], 'data_requests')
    simulated = dict(kind='data_requests', fingerprint=record['request_hash'], batch=task['batch'],
                     status='成功', transport_isolated=True, network_request_started=True, network_worker_reaped=True)
    task['ledger']['dispatches'].append(simulated)
    _record_current_acquisition(task, record, simulated)
    record['question_ids'] = ['q1']
    task['source_records'].append(record)
    spec = dict(id='synthetic_price', artifact_id=record['id'], object=CODE,
                record_locator=dict(table=0, row=0, field='close'), period=SESSION,
                nature='已实现事实', unit='元', raw_unit='元', raw_value=4.5, value=4.5, conversion_factor=1,
                published_at=None, publication=dict(at=None, basis_ref=None, precision='未知', timezone='未知',
                    certainty='未知', basis='合成接口响应，首次公开时间未知'),
                availability=current_snapshot_reference(task, record), question_ids=['q1'])
    direct = dict(spec, direct_code_reference=direct_code_reference(task, record))
    approved = candidate_evidence(task, record, direct)
    task['evidence'].append(agent.analyzer.assess(task, approved, review_for(PRICE_NEED), [record]))
    spec['identity'] = dict(provider_code=CODE, object=CODE, valid_from='2026-09-21', valid_to='2026-09-30',
                            basis='合成旧版的未核验声明')
    return task, record, spec


class IdentityPeriodFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='rh-identity-period-')
        self.addCleanup(directory.cleanup)
        agent = ResearchAgent(ResearchArchive(directory.name), ROOT, offline=True)
        self.addCleanup(agent.close_runtime)
        guard = patch('socket.socket.connect', side_effect=AssertionError('Identity fixtures cannot use network'))
        guard.start()
        self.addCleanup(guard.stop)
        clock = patch('researchhelper.evidence.now', return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.task, self.artifact, self.spec = quote_task(agent)
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
        spec['review'] = review_for(IDENTITY_NEED)
        self.witness_spec = copy.deepcopy(spec)
        proof = candidate_evidence(self.task, record, spec)
        proof = ResearchAnalyzer().assess(self.task, proof, spec['review'], self.task['materials'] + self.task['source_records'])
        self.task['evidence'].append(proof)
        self.spec['identity'].update(basis=statement, basis_ref={'version': 1,
            'evidence_id': proof['id'], 'evidence_hash': evidence_binding(proof), 'content_version': proof['content_version']})
        return proof

    def candidate(self):
        return candidate_evidence(self.task, self.artifact, self.spec)


class IdentityPeriodProvenanceTests(IdentityPeriodFixture):
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
        self.assertEqual(contract['version'], 1)
        self.assertEqual(contract['admissible_bases'][0]['identity'], self.spec['identity'])
        self.assertEqual(set(contract['basis_ref_fields']), set(self.spec['identity']['basis_ref']))
        self.assertEqual(self.task, before)

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
        direct = copy.deepcopy(self.task['evidence'][0])
        self.task['run_mode'] = 'host'
        with tempfile.TemporaryDirectory() as directory:
            ResearchAgent(ResearchArchive(directory), ROOT, offline=True)._step(self.task, 'evidence',
                {'evidence': [direct], 'evidence_specs': [price, invalid]})
        self.assertEqual(self.task['evidence'], [direct])
        self.assertEqual(len(self.task['rejected_evidence']), 2)

    def test_old_frozen_report_remains_readable_without_new_approval(self):
        # Construct the historical serialization in a disposable store.
        # Current admission cannot legitimately generate this v1 API receipt.
        legacy = copy.deepcopy(self.task['evidence'][0])
        legacy['identity'] = copy.deepcopy(self.spec['identity'])
        legacy['source_receipt'] = dict(verification_receipt(
            legacy, self.task, 'EvidenceCollector', '通过', '合成旧版记录'), version=1)
        legacy['applicability_receipt'] = verification_receipt(
            legacy, self.task, 'ResearchAnalyzer', '通过', '合成旧版记录')
        content = dict(schema='researchhelper.report-content', schema_version=1, task_id=self.task['id'],
                       research_version=self.task['version'], input_hash=self.task['input_hash'],
                       synthetic=True, evidence=[legacy])
        content['content_hash'] = fingerprint(content)
        paper = '<!doctype html><html lang="zh"><body><p>合成旧版行情4.5元。</p></body></html>'
        delivery = dict(version=self.task['version'], digest=fingerprint(content),
                        html_sha256=hashlib.sha256(paper.encode()).hexdigest(), saved_at=NOW)
        with tempfile.TemporaryDirectory(prefix='rh-legacy-identity-') as directory:
            archive = ResearchArchive(directory)
            saved = dict(self.task, evidence=[legacy], status='已完成', revision=0, delivery=delivery)
            archive.save(saved)
            with archive.connect() as db:
                db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?)',
                           (saved['id'], saved['version'], canonical(content), paper, delivery['digest'], NOW))
            reopened = ResearchArchive(directory)
            report = reopened.report(saved['id'], saved['version'])
            self.assertEqual(report['html'], paper)
            self.assertEqual(report['content']['evidence'][0]['source_receipt']['version'], 1)
            self.assertTrue(report['html_integrity_verified'])
            with self.assertRaisesRegex(ContractError, '同版来源引用'):
                validate_provenance(report['content']['evidence'][0], self.task)


if __name__ == '__main__':
    unittest.main()
