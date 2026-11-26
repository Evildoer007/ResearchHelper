"""New negative expectations for unregistered cross-original publication links.

Old test files are preserved. Their fabricated data, unit assertions, loopback
auth and checkpoint assertions are reused, but never described as approved
historical availability. The changed expectations are explicit in the receipt.
"""

import copy
import unittest

from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.argument_support import make_record, validate_adopted
from researchhelper.contracts import ContractError, validate_evidence
from researchhelper.evidence import EvidenceCollector, candidate_evidence, evidence_binding, extract_material, material_record
from researchhelper.model import IsolatedModelService, ModelService
from researchhelper.source_transport import IsolatedEvidenceCollector
from tests import test_market_background_sources_offline as market
from tests import test_autonomous_supplement_interfaces_offline as interfaces
from tests import test_publication_guard_api_compat_offline as api_compat
from tests.test_argument_support_workflow import assessed


class PublicationSourceBoundaryTests(unittest.TestCase):
    snapshot = market.MarketBackgroundSourceTests.snapshot
    extraction_only = api_compat.PublicationGuardAPICompatibilityTests.extraction_only
    reject_publication = api_compat.PublicationGuardAPICompatibilityTests.reject_publication

    def setUp(self):
        market.MarketBackgroundSourceTests.setUp(self)
        published = '发布日期：2026年9月20日。'
        statement = ('供应商：tinyshare；供应商代码：600000.SH；研究对象：600000.SH；'
                     '映射有效期起点：2026-09-01；映射有效期终点：2026-09-30')
        raw = (published + '\n\n' + statement).encode()
        material = material_record(self.task, extract_material('合成身份依据.txt', raw), raw)
        self.task['materials'].append(material)
        review = dict(question_ids=['q1'], allowed_natures=['已实现事实'],
                      conditions=['仅核对合成代码映射期间'], reason='依据为独立原件的完整声明')
        proposal = dict(id='identity_proof', artifact_id=material['id'], object='600000.SH',
            record_locator={'part': '段落2'}, text=statement, period='2026-09', nature='已实现事实',
            unit='不适用', question_ids=['q1'], published_at='2026-09-20',
            publication=dict(at='2026-09-20', basis='原件明确登记发布日期', precision='日',
                timezone='Asia/Shanghai', certainty='已确认', basis_ref=dict(kind='material',
                    artifact_id=material['id'], snapshot_hash=material['snapshot_hash'], part='段落1', quote=published)))
        witness = candidate_evidence(self.task, material, proposal)
        witness = ResearchAnalyzer().assess(self.task, witness, review, self.task['materials'])
        validate_evidence(witness, self.task)
        self.task['evidence'] = [witness]
        self.identity = dict(provider_code='600000.SH', object='600000.SH', basis=statement,
            valid_from='2026-09-01', valid_to='2026-09-30', basis_ref=dict(version=1, evidence_id=witness['id'],
                evidence_hash=evidence_binding(witness), content_version=witness['content_version']))

    def spec(self, artifact, field, raw_value, **changes):
        return market.MarketBackgroundSourceTests.spec(
            self, artifact, field, raw_value, **{'identity': copy.deepcopy(self.identity), **changes})

    def market_fixture(self):
        return self

    def test_registered_field_units_survive_but_borrowed_publication_is_rejected(self):
        api_compat.PublicationGuardAPICompatibilityTests.test_all_market_field_units_survive_with_candidate_cross_original_rejection(self)

    def test_missing_pe_and_negative_pb_preserved_without_false_availability(self):
        api_compat.PublicationGuardAPICompatibilityTests.test_missing_pe_and_negative_pb_retained_without_publication_approval(self)


class PublicationAppBoundaryTests(unittest.TestCase):
    stop = interfaces.SupplementInterfacesTests.stop
    request = interfaces.SupplementInterfacesTests.request
    login = interfaces.SupplementInterfacesTests.login

    def setUp(self):
        interfaces.SupplementInterfacesTests.setUp(self)
        agent = self.server.agent
        agent.draft_first = False  # Exercise formal verification and targeted supplementation.
        self.assertIsInstance(agent.model, IsolatedModelService)
        self.assertIsInstance(agent.collector, IsolatedEvidenceCollector)
        # Parent HTTP fixtures stay in the loopback-guarded process.
        agent.model = ModelService(agent.model.settings, offline=agent.model.offline, journal=agent.model.journal)
        agent.collector = EvidenceCollector(offline=agent.collector.offline, journal=agent.collector.journal)

    def test_supplier_supplement_preserves_answer_but_cannot_borrow_its_publication(self):
        # Exercise the canonical authenticated workflow directly. A copied test
        # body with edited assertions would drift whenever that workflow changes.
        interfaces.SupplementInterfacesTests.test_authenticated_legacy_transport_preserves_answer_without_certifying_raw_quote(self)
        saved = self.server.agent.archive.list()
        self.assertEqual(len(saved), 1)
        task = saved[0]
        self.assertEqual(task['questions'][1]['status'], '未回答')
        self.assertEqual([item['id'] for item in task['evidence']], ['publication'])
        self.assertTrue(any(item.get('id') == 'close_value' for item in task['rejected_evidence']))
        verify_preserved_review(self, task)


def verify_preserved_review(test, task):
    """Reject changed outputs or all corrupted approvals; allow a valid new review."""
    validate_adopted(task)
    mutations = (
        lambda value: value['questions'][0].update(answer='改写后的未复核答案'),
        lambda value: value['questions'][1].update(answer='缺少有效依据的收盘价'),
        lambda value: [record['result'].update(review_hash='篡改')
                       for record in value['argument_support_reviews']],
        lambda value: value.update(objects=['600519.SH']),
    )
    for index, mutate in enumerate(mutations):
        with test.subTest(review_mutation=index):
            changed = copy.deepcopy(task)
            mutate(changed)
            with test.assertRaises(ContractError):
                validate_adopted(changed)
    # The canonical fixture has a valid review against the current inputs.
    # An obsolete historical annotation cannot invalidate that independent review.
    changed_history = copy.deepcopy(task)
    changed_history['supplement_history'][0]['affected_questions'].append('q1')
    validate_adopted(changed_history)
    reassessed = copy.deepcopy(task)
    proposals = {record['review_key']: record['proposal'] for record in reassessed['argument_support_reviews']}
    for proposal in proposals.values():
        _, review = assessed(reassessed, proposal, rejected=('answer:q1.answer',))
        reassessed['argument_support_reviews'].append(make_record(reassessed, proposal, review, actor='host'))
    with test.assertRaisesRegex(ContractError, '报告回答未通过'):
        validate_adopted(reassessed)


REPLACED_EXPECTATIONS = {
    "tests.test_market_background_sources_offline.MarketBackgroundSourceTests.test_all_registered_fields_retain_units_and_two_verification_receipts":
        "tests.test_publication_relationship_offline.PublicationSourceBoundaryTests.test_registered_field_units_survive_but_borrowed_publication_is_rejected",
    "tests.test_market_background_sources_offline.MarketBackgroundSourceTests.test_missing_pe_is_never_promoted_to_zero_and_negative_pb_is_preserved":
        "tests.test_publication_relationship_offline.PublicationSourceBoundaryTests.test_missing_pe_and_negative_pb_preserved_without_false_availability",
    "tests.test_autonomous_supplement_interfaces_offline.SupplementInterfacesTests.test_authenticated_app_directed_supplier_round_preserves_independent_answer":
        "tests.test_publication_relationship_offline.PublicationAppBoundaryTests.test_supplier_supplement_preserves_answer_but_cannot_borrow_its_publication",
}
