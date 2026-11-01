"""Complete arrays, explicit business limits and legacy schemas stay distinct."""
import copy
import unittest

from tests import ROOT
from researchhelper.contracts import budget, canonical, context
from researchhelper.output_schema import (LOCAL_ARRAY_POLICY, MAX_ARGUMENT_CHARACTERS,
    StructuredOutputError, _matches, analysis_output_schema, decode_analysis_citations,
    object_schema, array_schema, provider_output_schema, research_collection_schema,
    schema_diagnostic, structured_json_completion, ANALYSIS_CITATION_PROTOCOL)


class CompleteResearchCollectionTests(unittest.TestCase):
    def setUp(self):
        self.task = dict(id='complete_collection_fixture', version=1, batch=1,
            cutoff='2026-10-19T15:00:00+08:00', input_hash='fixture',
            analysis_input_presentation_version=2, analysis_citation_encoding=2,
            analysis_question_scope_version=1, questions=[dict(id='q1')], calculations=[], ledger=budget(),
            evidence=[dict(id='row_'+str(i), question_ids=['q1'], value=i/100, unit='%') for i in range(300)])
        self.schema = analysis_output_schema(self.task)
        self.wire = dict(version=2, binding=context(self.task), theses=[], answers=[dict(
            question_id='q1', knowledge_refs=[], provenance_refs=[], segments=[dict(
                source='evidence', ref=item['id'], path='value', format='signed', digits=2)
                for item in self.task['evidence']])])

    def test_all_300_quantities_decode_without_trimming(self):
        result = decode_analysis_citations(self.wire, self.schema, self.task)
        answer = result['answers']['q1']
        self.assertEqual(len(answer['numeric_segments']['answer']), 300)
        self.assertEqual(set(answer['refs']), {e['id'] for e in self.task['evidence']})
        self.assertEqual(schema_diagnostic(self.wire, self.schema)['schema_issues'], [])

    def test_more_than_200_citations_are_kept_in_one_supported_statement(self):
        wire = copy.deepcopy(self.wire)
        wire['answers'][0]['segments'] = [dict(text='按原件逐项列示。',
            refs=[item['id'] for item in self.task['evidence']], counter_refs=[])]
        self.assertTrue(_matches(wire, self.schema))
        self.assertEqual(len(decode_analysis_citations(wire, self.schema, self.task)['answers']['q1']['refs']), 300)

    def test_new_array_policy_cannot_admit_wrong_value_path_or_foreign_reference(self):
        for field, value in [('ref', 'foreign'), ('path', 'raw_value'), ('source', 'calculation')]:
            wire = copy.deepcopy(self.wire)
            wire['answers'][0]['segments'][250][field] = value
            self.assertFalse(_matches(wire, self.schema))
            with self.assertRaises(StructuredOutputError):
                decode_analysis_citations(wire, self.schema, self.task)

    def test_explicit_limits_and_uniqueness_remain_authoritative(self):
        schema = research_collection_schema(object_schema(dict(rows=dict(
            array_schema(dict(type='integer')), minItems=1, maxItems=300, uniqueItems=True))), 2)
        self.assertTrue(_matches(dict(rows=list(range(300))), schema))
        self.assertFalse(_matches(dict(rows=list(range(301))), schema))
        self.assertFalse(_matches(dict(rows=[1, 1]), schema))
        self.assertFalse(_matches(dict(rows=[]), schema))

    def test_policy_never_leaves_local_schema_or_changes_legacy_tasks(self):
        self.assertEqual(self.schema[LOCAL_ARRAY_POLICY], 2)
        self.assertNotIn(LOCAL_ARRAY_POLICY, canonical(provider_output_schema(self.schema)))
        for version in (None, 1, True, 2.0, 3):
            task = copy.deepcopy(self.task)
            task['analysis_input_presentation_version'] = version
            schema = analysis_output_schema(task)
            self.assertNotIn(LOCAL_ARRAY_POLICY, schema)
            self.assertFalse(_matches(self.wire, schema))

    def test_total_response_character_budget_still_rejects_before_adoption(self):
        oversized = ' ' * (MAX_ARGUMENT_CHARACTERS + 1)
        with self.assertRaises(StructuredOutputError):
            structured_json_completion(oversized, self.schema, protocol=ANALYSIS_CITATION_PROTOCOL,
                                       context=self.task)


if __name__ == '__main__': unittest.main()
