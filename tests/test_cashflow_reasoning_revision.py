"""Versioned accounting guidance is not financial evidence or approval."""
import copy
import hashlib
import json
import unittest

from tests import ROOT
from researchhelper.argument_support import review_messages, review_request
from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.knowledge import ENTRIES, RETAINED_VERSIONS, ResearchKnowledge, extraction_guidance
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import analysis_contract

STUDY = ROOT/'result/acceptance/one-sentence-research-jqgawvhc'
PRIOR = {
    ('rh_k_cash_profit', 1): 'f650751afc1c63ca533fd94535bdb83db510ea0b935bef8ea57018f2f9f5425c',
    ('rh_k_net_margin', 1): '438ae50e5d0709f92f869544c5fcab82fd2b879a51d0f8d65c7f46acd1260e35',
    ('rh_k_cash_profit_ratio', 1): '89fc76c787afafd7824ca54040535bfe6602b9361c4f7e0ec03e7e442e9babf1',
    ('rh_k_net_margin', 2): 'dc2664bf3f0d28a7e1b43db4d11c32dda95be98f282e5e71de6571709dd23e22',
    ('rh_k_cash_profit_ratio', 2): '85cd4f3840ff6a39f2de40b3dd9c31ef5048ca0da0e8b34694178a307dc607b6',
}


class CashflowReasoningRevisionTests(unittest.TestCase):
    def setUp(self):
        self.authority = ResearchKnowledge()
        self.package = self.authority.read('个股', '盈利质量及经营现金流与净利润',
            methods=('net_profit_margin_v1', 'operating_cash_profit_ratio_v1'))
        self.old_raw = (STUDY/'task.json').read_bytes()
        self.old = json.loads(self.old_raw)

    def tearDown(self):
        self.assertEqual((STUDY/'task.json').read_bytes(), self.old_raw)

    def test_all_published_prior_versions_are_byte_identical(self):
        for key, expected in PRIOR.items():
            with self.subTest(key=key):
                self.assertEqual(fingerprint(RETAINED_VERSIONS[key]), expected)
        self.authority.validate_package(self.old['knowledge'])
        self.assertEqual(self.old['knowledge']['index']['rh_k_cash_profit'], 1)

    def test_current_methods_resolve_one_coherent_dependency_revision(self):
        self.authority.validate_package(self.package)
        self.assertEqual(self.package['index']['rh_k_cash_profit'], 2)
        self.assertEqual(len(self.package['entries']), len(self.package['index']))
        for method, ident in (('net_profit_margin_v1','rh_k_net_margin'),
                              ('operating_cash_profit_ratio_v1','rh_k_cash_profit_ratio')):
            current = next(entry for entry in self.package['entries'] if entry['id'] == ident)
            prior = copy.deepcopy(RETAINED_VERSIONS[(ident, 2)])
            self.assertEqual(current['version'], 3)
            self.assertEqual(self.authority.method_binding(self.package, method)['knowledge_version'], 3)
            self.assertEqual(self.authority.method_binding(self.package, method)['amount_units'],
                             ['元','万元','百万元'])
            for key in ('version','previous_version','revision_reason','dependencies'):
                prior.pop(key)
            compared = {key:value for key,value in current.items()
                        if key not in ('version','previous_version','revision_reason','dependencies')}
            self.assertEqual(compared, prior)
            self.assertEqual(current['dependencies'][0]['version'], 2)

    def test_knowledge_tampering_is_rejected_not_auto_adopted(self):
        modified = copy.deepcopy(self.package)
        mechanism = next(item for item in modified['entries'] if item['id']=='rh_k_cash_profit')
        mechanism['reasoning_checks'].clear()
        with self.assertRaises(ContractError):
            self.authority.validate_package(modified)

    def test_source_reading_receives_locked_checks_but_not_facts(self):
        task = copy.deepcopy(self.old)
        task['knowledge'] = copy.deepcopy(self.package)
        task['input_hash'] = input_fingerprint(task)
        before = canonical(task)
        guidance = extraction_guidance(task)
        mechanism = next(item for item in guidance['entries'] if item['id']=='rh_k_cash_profit')
        self.assertEqual(mechanism['version'], 2)
        self.assertEqual(mechanism['entry_hash'], fingerprint(RETAINED_VERSIONS[('rh_k_cash_profit', 2)]))
        self.assertFalse(guidance['financial_approval'])
        self.assertFalse(guidance['automatic_need_coverage'])
        self.assertEqual(canonical(task), before)
        self.assertNotIn('贵州茅台', canonical(guidance))
        self.assertNotIn('70,690', canonical(guidance))

    def test_independent_reviewer_receives_same_complete_locked_mechanism(self):
        task = copy.deepcopy(self.old)
        task['knowledge'] = copy.deepcopy(self.package)
        task['input_hash'] = input_fingerprint(task)
        before = canonical(task)
        # A real retained proposal is required; an empty body must remain
        # invalid rather than weakening the review boundary for this test.
        proposal = copy.deepcopy(self.old['argument_support_reviews'][-1]['proposal'])
        request = review_request(task, proposal)
        self.assertEqual(request['knowledge'], self.package['entries'])
        mechanism = next(item for item in request['knowledge'] if item['id']=='rh_k_cash_profit')
        self.assertEqual(mechanism, RETAINED_VERSIONS[('rh_k_cash_profit', 2)])
        self.assertTrue(mechanism['reasoning_checks'])
        self.assertIn('reasoning_checks', canonical(review_messages(request)))
        self.assertEqual(canonical(task), before)

    def test_no_cashflow_knowledge_for_unrelated_etf_question(self):
        package = self.authority.read('ETF', '比较基金管理费率及跟踪指数')
        self.assertNotIn('rh_k_cash_profit',package['index'])
        self.assertNotIn('rh_k_net_margin',package['index'])
        self.assertNotIn('rh_k_cash_profit_ratio',package['index'])

    def test_analysis_contract_projects_exact_locked_checks_without_new_obligations(self):
        task = copy.deepcopy(self.old)
        task['knowledge'] = copy.deepcopy(self.package)
        task['input_hash'] = input_fingerprint(task)
        before = canonical(task)
        contract = analysis_contract(task)
        guide = contract['client_writing']['knowledge_checks']
        self.assertFalse(guide['automatic_approval'])
        self.assertFalse(guide['automatic_scope_expansion'])
        self.assertEqual(set(contract['by_question']), {item['id'] for item in task['questions']})
        for item in guide['entries']:
            locked = RETAINED_VERSIONS[(item['id'],item['version'])]
            self.assertEqual(item['entry_hash'],fingerprint(locked))
            self.assertEqual(item['checks'],locked['reasoning_checks'])
        self.assertEqual(canonical(task),before)

    def test_legacy_analysis_does_not_silently_gain_new_knowledge_checks(self):
        before = canonical(self.old)
        self.assertNotIn('knowledge_checks',analysis_contract(self.old)['client_writing'])
        self.assertEqual(canonical(self.old),before)

    def test_concept_references_do_not_claim_first_publication(self):
        entry = RETAINED_VERSIONS[('rh_k_cash_profit', 2)]
        for reference in [entry['source_reference']] + entry['additional_source_references']:
            self.assertIsNone(reference['publication_date'])
            self.assertTrue(reference['sections'])
            self.assertTrue(reference['url'].startswith('https://'))
        self.assertEqual(entry['knowledge_role'], 'general_principle')
        self.assertNotIn('implementation', entry)

    def test_math_layout_success_does_not_override_semantic_rejection(self):
        review = json.loads((STUDY/'independent-semantic-review.json').read_text())
        self.assertFalse(review['report_accepted'])
        self.assertFalse(review['financial_quality_accepted'])
        self.assertFalse(review['study_reopened'])
        self.assertEqual(len(review['findings']), 3)
        self.assertTrue(review['other_scoped_evidence']['math_pdf_layout_scope_passed'])
        for name, expected in review['protected_sha256'].items():
            self.assertEqual(hashlib.sha256((STUDY/name).read_bytes()).hexdigest(), expected)
        self.assertEqual(self.old['status'], '已完成')
        self.assertTrue(self.old['argument_support_reviews'][-1]['result']['supported'])

