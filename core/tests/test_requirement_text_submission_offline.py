"""Explicit text references resolve only saved obligations, not financial facts."""

import copy
import unittest

from researchhelper.contracts import (ContractError, fingerprint, missing_evidence_requirements,
                                     validate_question_coverage, validate_evidence)
from researchhelper.reasoning import evidence_contract, PHASE_RULES
from researchhelper.reports import compose
from tests import test_query_provenance_offline as fixtures


class RequirementTextSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.QueryProvenanceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task, self.record = self.fixture.acquired()
        self.agent = self.fixture.agent
        self.need = fixtures.FACT_NEED
        self.ident = fingerprint(self.need)

    def review(self):
        return {'evidence_need_texts': {'q1': [self.need]}}

    def price(self):
        proposal = self.fixture.price(self.task, self.record)
        del proposal['review']['evidence_need_ids']
        proposal['review'].update(self.review())
        return proposal

    def complete(self):
        return self.agent.start(self.task['id'], {'evidence_specs': [self.price()],
            'answers': {'q1': {'answer': '本次响应记录该交易日收盘原值4.5元，查询范围见留痕。',
                'refs': ['price'], 'calculation_refs': [],
                'provenance_refs': [self.fixture.reference(self.task, self.record)]}}})

    def test_explicit_exact_text_resolves_ids_without_mutating_submission(self):
        review = self.review(); before = copy.deepcopy(review)
        self.assertEqual(validate_question_coverage(self.task, ['q1'], review), {'q1': [self.ident]})
        self.assertEqual(review, before)

    def test_text_in_legacy_id_field_still_rejects(self):
        with self.assertRaises(ContractError):
            validate_question_coverage(self.task, ['q1'], {'evidence_need_ids': {'q1': [self.need]}})

    def test_no_fuzzy_matching_new_text_duplicates_or_invalid_types(self):
        for value in ([self.need + ' '], [self.need[:5]], ['未登记需求'], [self.need, self.need],
                      [], self.need, None, [True]):
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_question_coverage(self.task, ['q1'], {'evidence_need_texts': {'q1': value}})
        with self.assertRaises(ContractError):
            validate_question_coverage(self.task, ['q1'], {'evidence_need_texts': None})

    def test_other_question_cannot_supply_the_text(self):
        with self.assertRaises(ContractError):
            validate_question_coverage(self.task, ['q1'], {'evidence_need_texts': {'q2': [self.need]}})
        task = copy.deepcopy(self.task)
        task['questions'][0]['evidence_needs'] = ['另一个实际问题']
        with self.assertRaises(ContractError):
            validate_question_coverage(task, ['q1'], self.review())

    def test_consistent_both_forms_support_canonical_and_retained_prefixed_id(self):
        for ident in (self.ident, 'q1:' + self.ident):
            review = dict(self.review(), evidence_need_ids={'q1': [ident]})
            self.assertEqual(validate_question_coverage(self.task, ['q1'], review), {'q1': [self.ident]})

    def test_conflicting_both_forms_and_duplicate_aliases_reject(self):
        for mapping in ({}, {'q1': [fingerprint(fixtures.TRACE_NEED)]},
                        {'q1': [self.ident, 'q1:' + self.ident]}):
            with self.subTest(mapping=mapping), self.assertRaises(ContractError):
                validate_question_coverage(self.task, ['q1'], dict(self.review(), evidence_need_ids=mapping))

    def test_old_id_only_review_remains_byte_for_byte_unchanged(self):
        review = {'evidence_need_ids': {'q1': ['q1:' + self.ident]}}
        before = copy.deepcopy(review)
        self.assertEqual(validate_question_coverage(self.task, ['q1'], review), review['evidence_need_ids'])
        self.assertEqual(review, before)

    def test_complete_source_checks_keep_original_text_and_canonical_ids(self):
        done = self.complete()
        self.assertEqual(done['status'], '已完成', done.get('gaps'))
        evidence = done['evidence'][0]
        self.assertEqual(evidence['applicability_review']['evidence_need_texts'], {'q1': [self.need]})
        self.assertEqual(evidence['applicability_review']['evidence_need_ids'], {'q1': [self.ident]})
        before = fingerprint(evidence)
        validate_evidence(evidence, done)
        self.assertEqual(fingerprint(evidence), before)
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertEqual(done['ledger']['used']['data_requests'], 1)
        self.assertEqual(compose(done)['questions'][0]['answer'], done['questions'][0]['answer'])

    def test_mutated_retained_text_or_id_is_rejected_before_export(self):
        done = self.complete()
        for field, value in (('evidence_need_texts', {'q1': ['篡改需求']}),
                             ('evidence_need_ids', {'q1': [fingerprint(fixtures.TRACE_NEED)]})):
            changed = copy.deepcopy(done)
            changed['evidence'][0]['applicability_review'][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                compose(changed)

    def test_query_text_cannot_substitute_for_acquisition_provenance(self):
        proposal = self.price()
        proposal['review']['evidence_need_texts']['q1'].append(fixtures.TRACE_NEED)
        done = self.agent.start(self.task['id'], {'evidence_specs': [proposal],
            'answers': {'q1': {'answer': '本次响应记录收盘原值4.5元。', 'refs': ['price']}}})
        self.assertEqual(done['status'], '部分完成')
        self.assertEqual(done['questions'][0]['status'], '未回答')
        self.assertEqual(len(done['evidence']), 1)
        self.assertEqual(missing_evidence_requirements(done['questions'][0], done['evidence']),
                         [{'id': fingerprint(fixtures.TRACE_NEED), 'text': fixtures.TRACE_NEED}])

    def test_text_selection_does_not_approve_wrong_raw_value_or_units(self):
        for field, value in (('raw_value', 45), ('unit', '点')):
            proposal = self.price(); proposal[field] = value
            with self.subTest(field=field):
                done = self.agent.start(self.task['id'], {'evidence_specs': [proposal]})
                self.assertFalse(done['evidence'])
                self.assertNotEqual(done['status'], '已完成')
                # Isolate attempts rather than restarting a completed task.
                self.task, self.record = self.fixture.acquired()

    def test_shared_grammar_makes_the_text_field_explicit(self):
        contract = evidence_contract(self.task)
        coverage = contract['question_coverage_contract']
        self.assertEqual(coverage['version'], 2)
        self.assertEqual(coverage['preferred_submission_field'], 'review.evidence_need_texts')
        self.assertFalse(coverage['reference_conversion_is_financial_approval'])
        self.assertEqual(contract['document_coverage_contract']['version'], 3)
        self.assertIn('推荐用review.evidence_need_texts', PHASE_RULES['extract'])
        self.assertEqual(contract['question_requirements']['q1'][0]['text'], self.need)
