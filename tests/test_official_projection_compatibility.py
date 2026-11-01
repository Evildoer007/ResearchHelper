"""New file/date encoding preserves registered official source shapes."""
import copy
import unittest

from tests import test_named_quantity_catalogue as fixtures
from researchhelper.evidence import _original_download_profile
from researchhelper.output_schema import (original_span_output_schema, original_span_prompt,
    decode_original_spans, _original_span_choices)
from researchhelper.planning import input_fingerprint


class OfficialProjectionCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.NamedQuantityCatalogueTests.setUpClass()
        cls.base = fixtures.NamedQuantityCatalogueTests()
        cls.task = copy.deepcopy(cls.base.task)
        cls.task.update(original_prompt_projection_version=2, original_period_selection_version=1,
                        original_question_link_version=1, original_observation_selection_version=1)
        cls.task['input_hash'] = input_fingerprint(cls.task)
        cls.views = cls.base.views
        cls.payload = copy.deepcopy(cls.base.payload)
        cls.schema = original_span_output_schema(cls.task, cls.views)
        cls.prompt = original_span_prompt(cls.payload, cls.task)

    @classmethod
    def tearDownClass(cls):
        fixtures.NamedQuantityCatalogueTests.tearDownClass()

    def wire(self):
        wire = self.base.wire()
        wire.update(version=12, binding_ref=self.prompt['span_binding_ref'])
        for item in wire['selections']:
            item.pop('question_ids')
            item.pop('period')
            item['temporal_reference'] = dict(observation_anchor=item.pop('observation_anchor'))
        return wire

    def test_direct_official_snapshot_is_not_treated_as_nested_ifind_snapshot(self):
        view_ids = {view['id'] for view in self.views}
        records = [record for record in self.task['source_records'] if record['id'] in view_ids
                   and record.get('source') == 'official' and record.get('operation') == 'release_original']
        self.assertTrue(records)
        for record in records:
            profile = _original_download_profile(record)
            self.assertIsNotNone(profile)
            self.assertEqual(profile['material'], record['snapshot'])
        self.assertEqual(len(_original_span_choices(self.task, self.views)), len(self.views))

    def test_monthly_quantity_retains_value_unit_and_actual_month_without_approval(self):
        result = decode_original_spans(self.wire(), self.schema, (self.task,self.views))
        expected = self.base.decode()['evidence_specs'][0]
        actual = result['evidence_specs'][0]
        for key in ('raw_token','raw_value','value','unit','period','observation_period'):
            self.assertEqual(actual.get(key), expected.get(key))
        self.assertNotIn('applicability_check', actual)

    def test_optional_reading_metadata_none_remains_none(self):
        self.assertIsNone(self.payload['source_reading'])
        self.assertIsNone(self.prompt['source_reading'])
        self.assertEqual(self.prompt['original_prompt_encoding']['version'], 2)
