"""The shared temporal selector preserves methodless monthly quantities."""
import copy
import unittest

from tests import test_named_quantity_catalogue as fixtures
from researchhelper.contracts import canonical
from researchhelper.output_schema import (original_span_output_schema, original_span_prompt, decode_original_spans,
    provider_request_schema, ORIGINAL_SPAN_PROTOCOL)
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS


class GeneralPeriodCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.NamedQuantityCatalogueTests.setUpClass()
        cls.base=fixtures.NamedQuantityCatalogueTests()
        cls.task=dict(copy.deepcopy(cls.base.task),original_question_link_version=1,original_period_selection_version=1)
        cls.views,cls.payload=cls.base.views,cls.base.payload
        cls.schema=original_span_output_schema(cls.task,cls.views)
        cls.prompt=original_span_prompt(cls.payload,cls.task)

    @classmethod
    def tearDownClass(cls):
        fixtures.NamedQuantityCatalogueTests.tearDownClass()

    def test_numeric_monthly_proposal_and_all_original_characters_are_unchanged(self):
        wire=self.base.wire();wire.update(version=12,binding_ref=self.prompt['span_binding_ref'])
        for item in wire['selections']:
            item.pop('question_ids');item.pop('period')
            item['temporal_reference']=dict(observation_anchor=item.pop('observation_anchor'))
        result=decode_original_spans(wire,self.schema,(self.task,self.views))
        self.assertEqual(result,self.base.decode())
        for original,view in zip(self.views,self.prompt['untrusted_sources']):
            parts=[dict(zip(view['part_columns'],row)) for row in view['parts']]
            self.assertEqual([p['text'] for p in original['parts']],
                             [''.join(line[1] for line in p['indexed_lines']) for p in parts])

    def test_complete_three_source_payload_still_fits_existing_cap(self):
        self.assertEqual(self.schema['properties']['version']['enum'],[12])
        wire_schema=provider_request_schema(self.task,self.schema,ORIGINAL_SPAN_PROTOCOL)
        size=len(canonical(self.prompt))+len(canonical(wire_schema))
        self.assertLessEqual(size,MAX_CONTEXT_CHARACTERS,size)
