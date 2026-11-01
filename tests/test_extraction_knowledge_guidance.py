"""Knowledge helps read original text; it never supplies ETF facts."""
import copy
import unittest

from tests import test_annual_fee_difference as fixtures
from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.knowledge import ResearchKnowledge, RETAINED_VERSIONS, extraction_guidance
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import evidence_contract, MAX_CONTEXT_CHARACTERS
from researchhelper.output_schema import original_span_prompt, original_span_output_schema

POLICY = 'extraction_knowledge_guidance_version'


class ExtractionKnowledgeGuidanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.AnnualFeeSourceContractTests.setUpClass()
        cls.base = fixtures.AnnualFeeSourceContractTests

    @classmethod
    def tearDownClass(cls):
        cls.base.tearDownClass()

    def setUp(self):
        self.task = dict(self.base.task, **{POLICY:1})
        self.task['knowledge'] = ResearchKnowledge().read('ETF', '比较ETF管理费率、托管费率及跟踪指数',
            context=dict(intent='比较对象', questions=[dict(id='q1', question='核对跟踪指数及管理费率')]))
        self.task['input_hash'] = input_fingerprint(self.task)

    def test_registered_revision_is_bound_and_not_evidence(self):
        guide = extraction_guidance(self.task)
        self.assertIsNotNone(guide)
        self.assertEqual(guide['input_hash'], self.task['input_hash'])
        self.assertFalse(guide['financial_approval'])
        self.assertFalse(guide['automatic_need_coverage'])
        item = next(row for row in guide['entries'] if row['id'] == 'rh_k_etf_exposure')
        self.assertEqual(item['version'], 2)
        self.assertEqual(item['entry_hash'], fingerprint(RETAINED_VERSIONS[(item['id'],2)]))
        self.assertIn('业绩比较基准', ''.join(item['checks']))
        self.assertNotIn('510300', canonical(guide))
        self.assertNotIn('159919', canonical(guide))

    def test_retained_version_keeps_its_immutable_body(self):
        package = copy.deepcopy(self.task['knowledge'])
        previous = RETAINED_VERSIONS[('rh_k_etf_exposure',1)]
        self.assertNotIn('extraction_checks', previous)
        package['entries'] = [copy.deepcopy(previous) if item['id']==previous['id'] else item
                              for item in package['entries']]
        package['index'][previous['id']] = 1
        ResearchKnowledge().validate_package(package)
        self.task['knowledge'] = package
        self.assertIsNone(extraction_guidance(self.task))

    def test_tampered_knowledge_or_changed_question_is_rejected(self):
        task = dict(self.task, knowledge=copy.deepcopy(self.task['knowledge']))
        task['knowledge']['entries'][0]['content'] += '伪造主体事实'
        with self.assertRaises(ContractError):extraction_guidance(task)
        with self.assertRaises(ContractError):extraction_guidance(dict(self.task, question='不同问题'))

    def test_legacy_document_and_empty_package_do_not_gain_guidance(self):
        from researchhelper.contracts import DOCUMENT_CONTENT_INTENT
        task = dict(self.task); task.pop(POLICY)
        self.assertIsNone(extraction_guidance(task))
        self.assertIsNone(extraction_guidance(dict(self.task, intent=DOCUMENT_CONTENT_INTENT)))
        self.assertIsNone(extraction_guidance(dict(self.task, knowledge=None)))
        for value in (True, False, 0, 2, 1.0):
            with self.subTest(value=value), self.assertRaises(ContractError):
                extraction_guidance(dict(self.task, **{POLICY:value}))

    def test_general_original_prompt_carries_guidance_without_hiding_sources(self):
        payload = dict(self.base.payload)
        payload['evidence_contract'] = evidence_contract(self.task, self.base.views)
        projected = original_span_prompt(payload, self.task)
        self.assertEqual(projected['evidence_contract']['knowledge_guidance'], extraction_guidance(self.task))
        self.assertEqual(projected['questions'], payload['questions'])
        self.assertEqual(len(projected['untrusted_sources']),len(self.base.views))
        schema = original_span_output_schema(self.task,self.base.views)
        self.assertLessEqual(len(canonical(projected))+len(canonical(schema)),MAX_CONTEXT_CHARACTERS)

    def test_policy_is_input_bound(self):
        task = dict(self.task); task.pop(POLICY)
        self.assertNotEqual(input_fingerprint(task), self.task['input_hash'])
