"""Exact numeric-owner grammar and bounded partial delivery, synthetic only."""

import copy
import json
import unittest

from researchhelper.contracts import ContractError
from researchhelper.numeric_integrity import (ANSWER_NUMERIC_FIELDS, THESIS_NUMERIC_FIELDS,
    numeric_segment_shape_errors, numeric_submission_contract)
from researchhelper.reasoning import (NumericSegmentFormatError, analysis_contract,
                                     validate_analysis_output)
from tests import test_analysis_partial_delivery_offline as partial


class NumericSubmissionShapeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = partial.AnalysisPartialDeliveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task, self.spec = self.fixture.policy()

    def test_owner_contract_has_no_cross_owner_or_empty_placeholders(self):
        contract = analysis_contract(self.task)
        self.assertEqual(contract['numeric_statement']['version'], 1)
        self.assertEqual(contract['numeric_statement']['submission_version'], 1)
        self.assertNotIn('fields', contract['numeric_statement'])
        self.assertEqual(contract['numeric_statement']['owner_fields'],
            {'answers': ['answer'], 'theses': ['title', 'investment_meaning', 'limitation']})
        self.assertEqual(contract['answer_item_structure']['numeric_segments']['allowed_fields'], ['answer'])
        self.assertFalse(contract['answer_item_structure']['numeric_segments']['additional_fields'])
        self.assertEqual(contract['thesis_item_structure']['numeric_segments'],
                         numeric_submission_contract(THESIS_NUMERIC_FIELDS))

    def test_grammar_and_shape_validator_agree_on_owner_fields(self):
        for fields in (ANSWER_NUMERIC_FIELDS, THESIS_NUMERIC_FIELDS):
            grammar = numeric_submission_contract(fields)
            for name in grammar['allowed_fields']:
                self.assertFalse(numeric_segment_shape_errors({name: [{'text': '结构示例'}]}, fields))
                self.assertTrue(numeric_segment_shape_errors({name: []}, fields))
            self.assertTrue(numeric_segment_shape_errors({'DO_NOT_LOG_FIELD': [{'text': '结构示例'}]}, fields))

    def test_malformed_numeric_container_has_diagnostic_without_private_fields(self):
        for value in ({'answer': []}, {'answer': 'DO_NOT_LOG_VALUE'},
                      {'DO_NOT_LOG_FIELD': [{'text': 'DO_NOT_LOG_TEXT'}]}, ['DO_NOT_LOG_VALUE']):
            task = copy.deepcopy(self.task)
            before = len(task['ledger']['dispatches'])
            task['ledger']['dispatches'].append({'kind': 'model_calls', 'status': '成功'})
            output = {'answers': {'q1': {'numeric_segments': value}}, 'theses': []}
            with self.subTest(value=value), self.assertRaises(NumericSegmentFormatError):
                validate_analysis_output(None, task, output, before)
            diagnostic = task['ledger']['dispatches'][-1]['output_contract']['item_structure']
            self.assertIn('numeric_containers', diagnostic)
            self.assertNotIn('DO_NOT_LOG', json.dumps(diagnostic))
            self.assertFalse(diagnostic['accepted'])

    def test_malformed_segments_keep_evidence_and_save_partial_not_success(self):
        proposal = self.fixture.fixture.answer()
        proposal['answers']['q1']['numeric_segments'] = {'answer': [], 'limitation': [{'text': '不采用'}]}
        done, model = self.fixture.fixture.run_model(self.task, [self.fixture.fixture.plan(self.task),
            {'evidence_specs': [self.spec]}, proposal])
        self.assertEqual(done['status'], '部分完成', done.get('error'))
        self.assertEqual(done['questions'][0]['status'], '未回答')
        self.assertEqual(len(done['evidence']), 1)
        self.assertTrue(done['delivery'])
        self.assertEqual(done['ledger']['used']['model_calls'], 3)
        self.assertEqual(len(model.requests), 3)
        self.assertNotIn('input_digest', done['reasoning_state']['analysis'])
        self.assertTrue(done['reasoning_state']['analysis']['output_rejected'])
        self.assertIn('数字片段格式未通过', self.fixture.fixture.agent.export(done['id'])['html'])

    def test_independent_definition_survives_numeric_format_failure(self):
        plan = self.fixture.fixture.plan(self.task)
        plan['questions'].append({'id': 'definition', 'question': '解释ETF口径', 'kind': 'fact',
            'answer_scope': 'general_definition', 'knowledge_needs': ['rh_k_etf_measures']})
        proposal = self.fixture.fixture.answer()
        proposal['answers']['q1']['numeric_segments'] = {'answer': []}
        done, _ = self.fixture.fixture.run_model(self.task, [plan, {'evidence_specs': [self.spec]}, proposal])
        self.assertEqual(done['status'], '部分完成', done.get('error'))
        self.assertEqual([q['status'] for q in done['questions']], ['未回答', '已回答'])
        self.assertTrue(done['delivery'])
        self.assertFalse(done['questions'][1]['knowledge_integrity']['fact_approval'])

    def test_non_numeric_structure_errors_are_not_promoted_to_recoverable_format(self):
        output = {'answers': {'q1': {'answer': ['invalid'], 'numeric_segments': {'answer': []}}}, 'theses': []}
        with self.assertRaises(ContractError) as caught:
            validate_analysis_output(None, self.task, output, len(self.task['ledger']['dispatches']))
        self.assertNotIsInstance(caught.exception, NumericSegmentFormatError)
        # A malformed quantity element is not a harmless owner-container error.
        malformed = {'answers': {'q1': {'numeric_segments': {'answer': ['invalid-quantity']}}}, 'theses': []}
        with self.assertRaises(ContractError) as caught:
            validate_analysis_output(None, self.task, malformed, len(self.task['ledger']['dispatches']))
        self.assertNotIsInstance(caught.exception, NumericSegmentFormatError)

    def test_quantity_shape_is_not_numeric_or_financial_approval(self):
        output = {'answers': {'q1': {'numeric_segments': {'answer': [
            {'source': 'calculation', 'ref': 'unregistered-result', 'path': 'value',
             'format': 'signed', 'digits': 2}]}, 'refs': [], 'calculation_refs': []}}, 'theses': []}
        validate_analysis_output(None, self.task, output, len(self.task['ledger']['dispatches']))
        from researchhelper.reasoning import analysis_numeric_review
        self.assertFalse(analysis_numeric_review(self.task, output)['accepted'])

    def test_empty_submitted_object_does_not_claim_any_generated_answer(self):
        self.assertTrue(numeric_segment_shape_errors({}, ANSWER_NUMERIC_FIELDS))
        # Omit an unused optional object; do not claim it contains an answer.
        from researchhelper.numeric_integrity import bind_numeric_statement
        with self.assertRaises(ContractError):
            bind_numeric_statement(self.task, {'numeric_segments': {}}, ['answer'], [], [])
