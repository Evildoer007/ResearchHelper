"""A qualitative original cannot become a numeric value through the UI model."""
import copy
import unittest

from researchhelper.contracts import ContractError
from researchhelper.output_schema import analysis_output_schema, decode_analysis_output


class AnalysisQuantityCatalogTests(unittest.TestCase):
    def setUp(self):
        self.task = {'questions': [{'id': 'q1'}], 'evidence': [{'id': 'quote', 'question_ids': ['q1'],
            'text': '订单不等于已经实现收入。', 'unit': '不适用'}], 'calculations': []}
        self.wire = {'version': 1, 'answers': [{'question_id': 'q1', 'segments': [{'text': '验收和成本存在限制。'}],
            'refs': ['quote'], 'calculation_refs': [], 'knowledge_refs': [], 'provenance_refs': []}], 'theses': []}

    def quantity(self, ref='quote', path='value', source='evidence'):
        return {'source': source, 'ref': ref, 'path': path, 'format': 'signed', 'digits': 2}

    def decode(self):
        return decode_analysis_output(self.wire, analysis_output_schema(self.task))

    def test_qualitative_text_preserves_its_complete_answer(self):
        self.assertEqual(self.decode()['answers']['q1']['numeric_segments']['answer'], self.wire['answers'][0]['segments'])

    def test_textual_evidence_never_gets_a_numeric_segment_branch(self):
        self.wire['answers'][0]['segments'] = [self.quantity()]
        with self.assertRaises(ContractError):
            self.decode()

    def test_empty_catalog_does_not_expose_fake_calculation_paths(self):
        self.task['evidence'] = []
        self.wire['answers'][0]['segments'] = [self.quantity('not-executed', source='calculation')]
        with self.assertRaises(ContractError):
            self.decode()

    def test_present_raw_quantity_is_preserved_not_recomputed(self):
        self.task['evidence'][0].update(raw_value=1.56, value=1.56, unit='%')
        self.wire['answers'][0]['segments'] = [self.quantity(path='raw_value')]
        self.assertEqual(self.decode()['answers']['q1']['numeric_segments']['answer'][0], self.quantity(path='raw_value'))

    def test_missing_path_is_not_offered_for_a_quantity(self):
        self.task['evidence'][0].update(raw_value=1.56, unit='%')
        self.wire['answers'][0]['segments'] = [self.quantity(path='value')]
        with self.assertRaises(ContractError):
            self.decode()

    def test_other_question_quantity_is_not_a_candidate(self):
        self.task['evidence'][0].update(raw_value=1.56, value=1.56, unit='%', question_ids=['q2'])
        self.wire['answers'][0]['segments'] = [self.quantity()]
        with self.assertRaises(ContractError):
            self.decode()

    def test_unknown_quantity_ref_is_rejected(self):
        self.task['evidence'][0].update(value=1.56, unit='%')
        self.wire['answers'][0]['segments'] = [self.quantity(ref='foreign')]
        with self.assertRaises(ContractError):
            self.decode()

    def test_saved_fee_summary_preserves_its_executed_quantity_paths(self):
        self.task['calculations'] = [{'id': 'fees', 'method': 'management_custody_fee_summary_v1',
            'question_ids': ['q1'], 'output': {'value': -.4, 'total_a': .2, 'total_b': .6}}]
        self.wire['answers'][0]['segments'] = [self.quantity('fees', 'total_a', 'calculation')]
        self.wire['answers'][0]['calculation_refs'] = ['fees']
        self.assertEqual(self.decode()['answers']['q1']['numeric_segments']['answer'][0]['path'], 'total_a')

    def test_planned_but_unexecuted_calculation_exposes_no_quantity(self):
        self.task['calculations'] = [{'id': 'fees', 'method': 'management_custody_fee_summary_v1',
            'question_ids': ['q1'], 'output': {}}]
        self.wire['answers'][0]['segments'] = [self.quantity('fees', 'value', 'calculation')]
        with self.assertRaises(ContractError):
            self.decode()


if __name__ == '__main__':
    unittest.main()
