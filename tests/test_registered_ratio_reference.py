"""Formula reference quantities are typed, scoped and never observed values."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from researchhelper.argument_support import review_request
from researchhelper.contracts import (ContractError, METHOD_REFERENCE_QUANTITIES,
    calculation_quantity_paths, method_reference_quantities)
from researchhelper.numeric_integrity import bind_numeric_statement, validate_numeric_statement
from researchhelper.output_schema import analysis_output_schema
from researchhelper.reasoning import analysis_contract

ROOT = Path(__file__).resolve().parents[1]
RETAINED = ROOT/'result/acceptance/one-sentence-research-xj6t95ng/task.json'


class RegisteredRatioReferenceTests(unittest.TestCase):
    def setUp(self):
        self.original = RETAINED.read_bytes()
        self.task = json.loads(self.original)
        self.calculation = next(item for item in self.task['calculations']
            if item['method']=='operating_cash_profit_ratio_v1')
        self.question = self.calculation['question_ids'][0]
        self.refs = self.calculation['input_refs']
        self.calc_refs = [self.calculation['id']]
        self.quantity = dict(source='calculation', ref=self.calculation['id'],
                             path='reference:parity', format='signed', digits=0)
        self.proposal = dict(question_ids=[self.question], numeric_segments=dict(answer=[
            dict(text='现金流与净利润相等时，对应的公式比例为'), self.quantity,
            dict(text='，该数学基准不是盈利质量合格线。')]))

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(), self.original)

    def bind(self, proposal=None, refs=None, calc_refs=None):
        return bind_numeric_statement(self.task, proposal or self.proposal, ['answer'],
            self.refs if refs is None else refs, self.calc_refs if calc_refs is None else calc_refs)

    def test_registered_formula_reference_is_bound_without_changing_calculated_output(self):
        before = copy.deepcopy(self.calculation)
        result = self.bind()
        self.assertIn('100%', result['fields']['answer'])
        binding = result['record']['fields']['answer']['bindings'][0]
        self.assertEqual(binding['projection'], 'registered_method_reference')
        self.assertEqual(binding['registered_reference']['definition']['version'], 1)
        self.assertIn('不是已观测数据', binding['registered_reference']['definition']['limitation'])
        self.assertEqual(self.calculation, before)
        self.assertNotIn('reference:parity', self.calculation['output'])
        self.assertNotIn('reference:parity', calculation_quantity_paths(self.calculation['method']))

    def test_no_calculation_or_missing_input_citation_is_rejected(self):
        with self.assertRaises(ContractError): self.bind(calc_refs=[])
        with self.assertRaises(ContractError): self.bind(refs=self.refs[:-1])

    def test_reference_is_not_available_on_net_margin_or_unregistered_methods(self):
        margin = next(item for item in self.task['calculations'] if item['method']=='net_profit_margin_v1')
        proposal = copy.deepcopy(self.proposal)
        proposal['numeric_segments']['answer'][1]['ref'] = margin['id']
        with self.assertRaises(ContractError): self.bind(proposal, margin['input_refs'], [margin['id']])
        self.assertFalse(method_reference_quantities('invented_method'))

    def test_unknown_reference_and_user_supplied_value_do_not_become_constants(self):
        for changes in (dict(path='reference:quality'), dict(value=100), dict(path='value', value=100)):
            proposal = copy.deepcopy(self.proposal)
            proposal['numeric_segments']['answer'][1].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ContractError): self.bind(proposal)

    def test_plain_percentage_stays_rejected_even_when_a_reference_exists(self):
        proposal = copy.deepcopy(self.proposal)
        proposal['numeric_segments']['answer'] = [dict(text='经营现金流覆盖高于100%。')]
        with self.assertRaises(ContractError): self.bind(proposal)

    def test_reference_cannot_cross_task_or_research_version(self):
        for field, value in (('task_id', 'foreign'), ('research_version', self.task['version']+1)):
            calculation = copy.deepcopy(self.calculation)
            calculation[field] = value
            self.task['calculations'] = [calculation]
            with self.subTest(field=field), self.assertRaises(ContractError): self.bind()

    def test_reference_cannot_be_formatted_as_a_relative_return_comparison(self):
        proposal = copy.deepcopy(self.proposal)
        proposal['numeric_segments']['answer'][1]['format'] = 'comparison'
        with self.assertRaises(ContractError): self.bind(proposal)

    def test_definition_change_invalidates_the_saved_numeric_receipt(self):
        bound = self.bind()
        proposal = dict(self.proposal, answer=bound['fields']['answer'], numeric_integrity=bound['record'])
        validate_numeric_statement(self.task, proposal, ['answer'], self.refs, self.calc_refs)
        registry = copy.deepcopy(METHOD_REFERENCE_QUANTITIES)
        registry[self.calculation['method']]['reference:parity']['version'] += 1
        with patch.dict(METHOD_REFERENCE_QUANTITIES, registry, clear=True), self.assertRaises(ContractError):
            validate_numeric_statement(self.task, proposal, ['answer'], self.refs, self.calc_refs)

    def test_reference_registry_returns_an_independent_copy(self):
        values = method_reference_quantities(self.calculation['method'])
        values['reference:parity']['value'] = 7
        self.assertEqual(method_reference_quantities(self.calculation['method'])['reference:parity']['value'], 100)

    def test_formal_analysis_catalogue_and_schema_offer_the_typed_path(self):
        contract = analysis_contract(self.task)
        references = contract['by_question'][self.question]['numeric_quantity_references']
        self.assertTrue(any(item['ref']==self.calculation['id'] and item['path']=='reference:parity'
                            for item in references))
        self.assertIn('reference:parity', json.dumps(analysis_output_schema(self.task)))

    def test_actual_failed_proposal_only_changes_representation_in_controlled_replay(self):
        proposal = copy.deepcopy(self.task['host_result'])
        with self.assertRaises(ContractError): review_request(self.task, proposal)
        # Forensic control only. Production never rewrites rejected model text.
        for item in [*proposal['answers'].values(), *proposal['theses']]:
            for field, parts in item['numeric_segments'].items():
                converted = []
                for part in parts:
                    if set(part)!={'text'} or '100%' not in part['text']:
                        converted.append(part)
                        continue
                    pieces = part['text'].split('100%')
                    for index, text in enumerate(pieces):
                        if index: converted.append(copy.deepcopy(self.quantity))
                        if text: converted.append(dict(text=text))
                item['numeric_segments'][field] = converted
        request = review_request(self.task, proposal)
        targets = [item for item in request['targets'] if 'method_references' in item]
        self.assertTrue(targets)
        for target in targets:
            self.assertIn('100%', target['text'])
            self.assertTrue(all(item['definition']['value']==100 for item in target['method_references']))
        self.assertFalse(self.task.get('argument_support_reviews'))
