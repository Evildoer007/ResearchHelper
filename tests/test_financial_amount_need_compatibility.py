"""Actual R121 plan/originals: typed amounts retain their complete source row.

Explicit selections test the shared gates, not recovery of a discarded model
response, an autonomous rerun, approved analysis or a completed report.
"""
import copy
import json
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from researchhelper.contracts import fingerprint, registered_financial_bindings
from researchhelper.data_requirements import satisfies, pending
from researchhelper.output_schema import (financial_reference_output_schema, financial_reference_prompt,
    decode_financial_references, _matches, _financial_amount_need_compatible)
from researchhelper.reasoning import evidence_contract, validate_model_specs, expand_financial_value_references
from researchhelper.source_reader import validate_read_specs
from researchhelper.evidence import candidate_evidence
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import ResearchKnowledge

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('RESEARCHHELPER_AMOUNT_NEEDS_INPUT', str(
    ROOT/'result/acceptance/one-sentence-research-itcqfraw/task.json')))


class FinancialAmountNeedCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = SOURCE.read_bytes()
        cls.original = json.loads(cls.raw)

    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Retained originals only'))
        guard.start(); self.addCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); self.addCleanup(worker.stop)
        self.task = copy.deepcopy(self.original)
        self.bundle = self.task['reasoning_state']['reading']['bundle']
        self.views = self.bundle['views']
        self.record = next(record for record in self.task['source_records'] if record['id'] == self.views[0]['id'])
        self.contract = evidence_contract(self.task, self.views)
        self.schema = financial_reference_output_schema(self.task, self.views)
        self.prompt = financial_reference_prompt(dict(untrusted_sources=self.views, evidence_contract=self.contract), self.task)
        self.question = next(question for question in self.task['questions'] if question['id'] == 'q4')
        self.need = next(text for text in self.question['evidence_needs'] if '主要调节项目及对应金额' in text)
        self.assertEqual(self.question['evidence_need_constraints'][self.need]['value_kind'], 'text')
        self.catalogue = self.contract['sources'][0]['financial_amount_tables']

    def tearDown(self):
        self.assertEqual(SOURCE.read_bytes(), self.raw)

    def wire(self):
        amounts = []
        observation = next(choice['reference'] for choice in self.prompt['untrusted_sources'][0]['observation_choices']
                           if choice['date'] == '2026-06-30')
        for index, label in enumerate(('固定资产折旧', '存货的减少', '经营性应收', '经营性应付'), 1):
            table, ref = next((table, ref) for table in self.catalogue['tables']
                for ref, row in table['rows'].items() if label in row['text'].split('\t')[0])
            column = next(column for column in table['columns'] if column['column_index'] == 1 and ref in column['row_references'])
            amounts.append(dict(id='actual_plan_control_'+str(index), object=self.record['directory']['issuer'],
                nature='已实现事实', table_reference=table['reference'], column_reference=column['reference'],
                row_reference=ref, observation_reference=observation,
                review=dict(scope='current_original_descriptive_only', need_links={qid:
                    ([fingerprint(self.need)] if qid == 'q4' else None) for qid in self.contract['question_ids']})))
        return dict(version=9, source_reference=self.prompt['financial_reference_binding'],
                    current_version_only=True, financial_cells=[], text_quotes=[], amount_cells=amounts)

    def test_actual_amount_rows_can_support_the_actual_text_need_without_losing_numeric_values(self):
        wire = self.wire(); before = copy.deepcopy(wire)
        self.assertTrue(_matches(wire, self.schema))
        specs = decode_financial_references(wire, self.schema, (self.task, self.views))['evidence_specs']
        self.assertEqual([item['value'] for item in specs], [14282273, -37859085, -33026925, 71690439])
        for item in specs:
            self.assertTrue(satisfies(item, self.question['evidence_need_constraints'][self.need]))
            self.assertEqual(item['text'], item['record_locator']['financial_amount_column']['row_quote'])
            self.assertEqual(item['unit'], '千元')
            self.assertNotIn('calculation_context', item)
        self.assertEqual(wire, before)

    def test_ordinary_adoption_covers_only_the_selected_need_not_answers_or_calculations(self):
        before = copy.deepcopy(self.task['ledger'])
        specs = decode_financial_references(self.wire(), self.schema, (self.task, self.views))['evidence_specs']
        validate_model_specs(self.task, specs)
        validate_read_specs(self.task, specs, self.bundle)
        analyzer = ResearchAnalyzer()
        for spec in specs:
            item = analyzer.assess(self.task, candidate_evidence(self.task, self.record, spec), spec['review'], self.task['source_records'])
            self.assertEqual((item['source_check'], item['applicability_check']), ('通过', '通过'))
            self.task['evidence'].append(item)
        remaining = pending(self.task)
        self.assertFalse(any(item['question_id'] == 'q4' and item['text'] == self.need for item in remaining))
        self.assertTrue(any(item['question_id'] == 'q4' for item in remaining))
        self.assertEqual(self.task['ledger'], before)
        self.assertEqual(self.task['status'], '部分完成')
        self.assertFalse(self.task['calculations'] or self.task['theses'])

    def test_numeric_percentage_and_point_controls_are_not_discharged_by_interval_amounts(self):
        # Test the same predicate used to construct each actual column's Schema.
        # The closed plan and its knowledge fingerprint must remain untouched.
        base = self.question['evidence_need_constraints'][self.need]
        item = dict(text='存货的减少\t-37,859,085\t-24,132,701',
                    value=-37859085, unit='千元', period='2026年半年度')
        for field, value in (('unit_dimension', 'percentage'), ('period_kind', 'point')):
            spec = copy.deepcopy(base)
            if field == 'unit_dimension': spec.update(value_kind='number', unit_dimension=value)
            else: spec[field] = value
            with self.subTest(field=field):
                self.assertFalse(_financial_amount_need_compatible(spec, item['period']))
                self.assertFalse(satisfies(item, spec))

    def test_actual_fact_question_inputs_bind_both_main_question_calculations_without_rewriting_the_plan(self):
        before = copy.deepcopy(self.task['ledger'])
        questions = copy.deepcopy(self.task['questions'])
        source = self.contract['sources'][0]
        fact = next(question for question in self.task['questions'] if question['id'] == 'q3')
        wire = self.wire(); wire['amount_cells'] = []
        observation = next(choice['reference'] for choice in self.prompt['untrusted_sources'][0]['observation_choices']
                           if choice['date'] == '2026-06-30')
        for metric, label in (('revenue', '营业收入'), ('net_profit', '合并利润表净利润'),
                              ('operating_cash_flow', '经营活动产生的现金流量净额')):
            candidate = next(item for item in source['financial_context_cells']
                if item['context']['metric'] == metric and '2026' in item['context']['period'])
            need = next(text for text in fact['evidence_needs'] if label in text)
            wire['financial_cells'].append(dict(id='actual_plan_'+metric, object=self.record['directory']['issuer'],
                nature='已实现事实', financial_context_reference=candidate['candidate_id'],
                shared_context_references={key: source['financial_shared_context_choices'][key][0]['candidate_id']
                    for key in ('entity_id', 'accounting_standard')}, observation_reference=observation,
                review=dict(scope='current_original_descriptive_only', need_links={qid:
                    ([fingerprint(need)] if qid == 'q3' else None) for qid in self.contract['question_ids']})))
        decoded = decode_financial_references(wire, self.schema, (self.task, self.views))['evidence_specs']
        specs = expand_financial_value_references(self.task, decoded, self.views)
        validate_model_specs(self.task, specs); validate_read_specs(self.task, specs, self.bundle)
        analyzer = ResearchAnalyzer()
        for spec in specs:
            item = analyzer.assess(self.task, candidate_evidence(self.task, self.record, spec), spec['review'], self.task['source_records'])
            self.assertEqual((item['source_check'], item['applicability_check']), ('通过', '通过'))
            self.task['evidence'].append(item)
        proposal = registered_financial_bindings(self.task, ['q1'])
        self.assertIsNotNone(proposal)
        agent = ResearchAgent.__new__(ResearchAgent)
        agent.knowledge = ResearchKnowledge()
        for spec in proposal['calculations']:
            agent._calculate_spec(self.task, spec)
        results = {item['method']: item for item in self.task['calculations']}
        self.assertAlmostEqual(results['net_profit_margin_v1']['output']['value'], 47030638/276916580*100)
        self.assertAlmostEqual(results['operating_cash_profit_ratio_v1']['output']['value'], 60216851/47030638*100)
        self.assertEqual(self.task['questions'], questions)
        self.assertEqual(self.task['ledger'], before)
        self.assertEqual(self.task['status'], '部分完成')
        self.assertFalse(self.task['theses'])


if __name__ == '__main__':
    unittest.main()
