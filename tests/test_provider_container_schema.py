"""Empty-container wire encoding is schema-directed and lossless, not repair."""
import copy
import unittest
from researchhelper.output_schema import (object_schema,array_schema,provider_output_schema,
    decode_provider_containers,EMPTY_OBJECT_WIRE,_matches)


class ProviderContainerSchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema=object_schema({'values':array_schema(object_schema({'parameters':object_schema({}),
            'text':{'type':'string'}}))})
        self.wire={'values':[{'parameters':EMPTY_OBJECT_WIRE,'text':EMPTY_OBJECT_WIRE}]}

    def test_nested_container_round_trip_preserves_literal_text(self):
        decoded=decode_provider_containers(self.wire,self.schema)
        self.assertEqual(decoded,{'values':[{'parameters':{},'text':EMPTY_OBJECT_WIRE}]})
        self.assertTrue(_matches(decoded,self.schema))

    def test_provider_schema_contains_no_empty_objects(self):
        compiled=provider_output_schema(self.schema)
        self.assertEqual(compiled['properties']['values']['items']['properties']['parameters'],
                         {'type':'string','enum':[EMPTY_OBJECT_WIRE]})
        self.assertTrue(_matches(self.wire,compiled))

    def test_caller_schema_and_wire_are_not_mutated(self):
        before=copy.deepcopy((self.schema,self.wire))
        provider_output_schema(self.schema);decode_provider_containers(self.wire,self.schema)
        self.assertEqual((self.schema,self.wire),before)

    def test_legacy_canonical_containers_still_work(self):
        value={'values':[{'parameters':{},'text':'literal'}]}
        self.assertEqual(decode_provider_containers(value,self.schema),value)

    def test_unknown_keys_are_not_deleted_to_make_a_submission_pass(self):
        self.wire['values'][0]['parameters']={'approved':True}
        decoded=decode_provider_containers(self.wire,self.schema)
        self.assertFalse(_matches(decoded,self.schema))
        self.assertEqual(decoded['values'][0]['parameters'],{'approved':True})

    def test_wrong_marker_does_not_become_empty_parameters(self):
        self.wire['values'][0]['parameters']='invented'
        decoded=decode_provider_containers(self.wire,self.schema)
        self.assertFalse(_matches(decoded,self.schema))

    def test_unambiguous_union_uses_only_its_declared_container_shape(self):
        schema={'anyOf':[self.schema,object_schema({'other':{'type':'string'}})]}
        result=decode_provider_containers(self.wire,schema)
        self.assertEqual(result['values'][0]['parameters'],{})
        self.assertTrue(_matches(result,schema))
