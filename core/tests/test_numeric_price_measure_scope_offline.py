"""Explicit price-measure label checks, not natural-language entailment tests."""

import copy
import unittest

from researchhelper.contracts import ContractError
from researchhelper.numeric_integrity import bind_numeric_statement, measure_scope_contract, validate_numeric_statement


class NumericPriceMeasureScopeTests(unittest.TestCase):
    def setUp(self):
        self.task = {'id': 'scope_fixture', 'version': 1, 'questions': [{'id': 'q1'}],
                     'evidence': [], 'calculations': []}
        for ref, value in [('start', 10), ('end', 11)]:
            self.task['evidence'].append({'id': ref, 'object': '合成标的', 'value': value, 'raw_value': value,
                'unit': '元', 'raw_unit': '元', 'task_id': self.task['id'], 'research_version': 1, 'question_ids': ['q1']})
        self.task['calculations'] = [{'id': 'price', 'method': 'observed_price_return_v1',
            'input_refs': ['start', 'end'], 'input_bindings': {'start_price': 'start', 'end_price': 'end'},
            'output': {'value': 10, 'unit': '%'}, 'task_id': self.task['id'], 'research_version': 1, 'question_ids': ['q1']}]
        self.quantity = {'source': 'calculation', 'ref': 'price', 'path': 'value', 'format': 'signed', 'digits': 2}

    def bind(self, text=None, parts=None):
        proposal = {'question_ids': ['q1']}
        if text is not None:
            proposal['answer'] = text
        if parts is not None:
            proposal['numeric_segments'] = {'answer': parts}
        return bind_numeric_statement(self.task, proposal, ['answer'], ['start', 'end'], ['price'])

    def test_price_change_with_explicit_limitations_remains_valid(self):
        for text in ('未复权价格变化为10.00%，不代表持有人总回报。',
                     '价格变化为10.00%不是基金净值收益。',
                     '总回报未核验，未复权价格变化为10.00%。',
                     '不是总回报，价格变化为10.00%。'):
            with self.subTest(text=text):
                self.assertEqual(self.bind(text)['fields']['answer'], text)

    def test_correct_number_cannot_license_a_different_return_kind(self):
        for label in ('持有人总回报', '持有收益率', '基金净值收益', '总收益率', '投资收益',
                      '实际收益率', '年化收益率', '超额收益', '上涨概率', '事件贡献'):
            with self.subTest(label=label), self.assertRaises(ContractError):
                self.bind(label + '为10.00%。')

    def test_suffix_labels_also_require_their_actual_measure(self):
        for label in ('持有人总回报', '基金净值收益率', '上涨概率', '因果贡献'):
            with self.subTest(label=label), self.assertRaises(ContractError):
                self.bind('录得10.00%的' + label + '。')

    def test_explicit_segments_cannot_mislabel_the_selected_price_source(self):
        for parts in ([{'text': '持有人总回报为'}, self.quantity],
                      [self.quantity, {'text': '的基金净值收益。'}]):
            with self.subTest(parts=parts), self.assertRaises(ContractError):
                self.bind(parts=parts)
        result = self.bind(parts=[{'text': '价格变化为'}, self.quantity,
                                  {'text': '，不代表持有人总回报。'}])
        self.assertIn('10.00%', result['fields']['answer'])

    def test_saved_binding_cannot_be_reused_after_changing_measure_label(self):
        valid = self.bind('未复权价格变化为10.00%。')
        proposal = {'question_ids': ['q1'], 'answer': valid['fields']['answer'], 'numeric_integrity': valid['record']}
        validate_numeric_statement(self.task, proposal, ['answer'], ['start', 'end'], ['price'])
        changed = copy.deepcopy(proposal)
        changed['answer'] = '持有人总回报为10.00%。'
        with self.assertRaises(ContractError):
            validate_numeric_statement(self.task, changed, ['answer'], ['start', 'end'], ['price'])

    def test_index_change_is_not_fund_holder_return(self):
        self.task['calculations'][0]['method'] = 'observed_index_change_v1'
        self.assertEqual(self.bind('全收益指数变化为10.00%。')['fields']['answer'], '全收益指数变化为10.00%。')
        with self.assertRaises(ContractError):
            self.bind('持有人总回报为10.00%。')

    def test_public_contract_does_not_claim_general_semantic_verification(self):
        contract = measure_scope_contract()
        self.assertEqual(contract['version'], 1)
        self.assertIn('显式', contract['limitation'])
        self.assertIn('不是任意金融语义', contract['limitation'])
        contract['restricted_methods'].clear()
        self.assertEqual(len(measure_scope_contract()['restricted_methods']), 2)
