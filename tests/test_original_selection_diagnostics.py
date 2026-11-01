"""Rejected field format is not misreported as a proven task-binding failure."""
import copy
import json
import unittest

from tests import ROOT
from tests import test_multi_original_capacity as fixtures
from researchhelper.output_schema import StructuredOutputError, original_span_output_schema, decode_original_spans


class OriginalSelectionDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.MultiOriginalCapacityTests()
        fixtures.MultiOriginalCapacityTests.setUpClass()
        cls.task = copy.deepcopy(cls.fixture.task)
        cls.task['original_span_diagnostics_version'] = 1
        cls.views = cls.fixture.views
        cls.schema = original_span_output_schema(cls.task, cls.views)

    @classmethod
    def tearDownClass(cls):
        fixtures.MultiOriginalCapacityTests.tearDownClass()

    def test_unknown_field_is_not_reported_as_a_changed_binding(self):
        wire = self.fixture.wire()
        wire['PRIVATE_UNKNOWN_FIELD'] = 'PRIVATE_UNREGISTERED_VALUE'
        with self.assertRaises(StructuredOutputError) as caught:
            decode_original_spans(wire, self.schema, (self.task, self.views))
        error = caught.exception
        self.assertIn('字段不符合', str(error))
        self.assertEqual(error.diagnostic['schema_issues'][0]['code'], 'unknown_fields')
        self.assertNotIn('PRIVATE_', json.dumps(error.diagnostic))

    def test_wrong_bound_wire_field_has_registered_field_path_only(self):
        wire = self.fixture.wire()
        wire['binding_ref'] = 'PRIVATE_FOREIGN_BINDING'
        with self.assertRaises(StructuredOutputError) as caught:
            decode_original_spans(wire, self.schema, (self.task, self.views))
        self.assertIn(dict(path='$.binding_ref', code='enum_mismatch'), caught.exception.diagnostic['schema_issues'])
        self.assertFalse(caught.exception.diagnostic['submitted_values_retained'])
        self.assertNotIn('PRIVATE_FOREIGN', json.dumps(caught.exception.diagnostic))

    def test_forged_schema_is_not_diagnosed_as_a_model_field_error(self):
        schema = copy.deepcopy(self.schema)
        schema['properties']['binding_ref']['enum'] = ['forged']
        with self.assertRaises(StructuredOutputError) as caught:
            decode_original_spans(self.fixture.wire(), schema, (self.task, self.views))
        self.assertEqual(caught.exception.diagnostic['schema_issues'], [dict(path='$', code='context_schema_mismatch')])

    def test_valid_choices_still_decode_identically_without_approval(self):
        wire = self.fixture.wire()
        actual = decode_original_spans(wire, self.schema, (self.task, self.views))
        expected = decode_original_spans(wire, self.schema, (self.fixture.task, self.views))
        self.assertEqual(actual, expected)
        self.assertNotIn('approved', actual)

    def test_legacy_rejection_keeps_its_original_category_and_no_new_diagnostic(self):
        wire = self.fixture.wire()
        wire['extra'] = 'not adopted'
        with self.assertRaises(StructuredOutputError) as caught:
            decode_original_spans(wire, self.schema, (self.fixture.task, self.views))
        self.assertEqual(caught.exception.category, 'invalid_original_spans')
        self.assertEqual(caught.exception.diagnostic, {})
