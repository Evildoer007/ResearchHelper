"""Finite-scope equivalence, including incompatible paths and other questions."""
import copy
import itertools
import json
import unittest

from tests import ROOT
from tests import test_question_bound_analysis as question_fixture
from researchhelper.contracts import canonical
from researchhelper.output_schema import analysis_output_schema, decode_analysis_citations, _matches
from researchhelper.model_presentation import restore


class QuantitySchemaGroupingTests(unittest.TestCase):
    def setUp(self):
        fixture = question_fixture.QuestionBoundAnalysisTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.task = fixture.task
        self.wire = fixture.wire
        self.task['evidence'][0]['raw_value'] = '100.00'
        self.task['evidence'].append(dict(id='text_only', question_ids=['q1'], unit='不适用',
                                         text='原文描述', value=None))

    def test_grouped_choices_accept_exactly_same_finite_combinations(self):
        original = analysis_output_schema(self.task)
        updated = copy.deepcopy(self.task)
        updated['analysis_input_presentation_version'] = 1
        grouped = analysis_output_schema(updated)
        self.assertLess(len(canonical(grouped)), len(canonical(original)))
        for question, source, ref, path in itertools.product(
                ('q1', 'q2', 'q3'), ('evidence', 'calculation'),
                ('one', 'two', 'shared', 'margin', 'text_only', 'foreign'),
                ('value', 'raw_value', 'premium', 'other')):
            wire = copy.deepcopy(self.wire)
            wire['answers'][0].update(question_id=question, segments=[dict(
                source=source, ref=ref, path=path, format='signed', digits=2)])
            with self.subTest(question=question, source=source, ref=ref, path=path):
                self.assertEqual(_matches(wire, original), _matches(wire, grouped))
                if _matches(wire, original):
                    self.assertEqual(decode_analysis_citations(wire, original, self.task),
                                     decode_analysis_citations(wire, grouped, updated))
        self.assertNotIn('analysis_input_presentation_version', self.task)

    def test_review_only_changes_presentation_not_citation_permissions(self):
        from researchhelper import argument_support as support
        saved = json.loads((ROOT / 'result/acceptance/one-sentence-research-2oro8x00/task.json').read_text())
        proposal = saved['argument_support_reviews'][0]['proposal']
        request = support.review_request(saved, proposal)
        ordinary = support.review_messages(request, use_reference_units=True)
        updated = copy.deepcopy(request)
        updated['scope']['input_presentation_version'] = 1
        messages = support.review_messages(updated, use_reference_units=True)
        actual = json.loads(messages[1]['content'])
        if actual.get('format') == 'researchhelper.table_input':
            actual = restore(actual)
        expected = json.loads(ordinary[1]['content'])
        expected['scope']['input_presentation_version'] = 1
        self.assertEqual(canonical(actual), canonical(expected))
        self.assertEqual(support.reference_basis_choices(request), support.reference_basis_choices(updated))
        self.assertNotIn('input_presentation_version', request['scope'])


if __name__ == '__main__': unittest.main()
