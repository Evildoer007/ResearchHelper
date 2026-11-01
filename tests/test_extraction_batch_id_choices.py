"""Finite explicit identifiers do not auto-rename or approve source content."""
import copy
import gzip
import json
import unittest

from tests import ROOT
from researchhelper.output_schema import original_span_output_schema, _matches
from researchhelper.planning import input_fingerprint


class ExtractionBatchIdChoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with gzip.open(ROOT/'result/acceptance/autonomous-data-phase-zk4o3hai/task.json.gz','rt',encoding='utf-8') as stream:
            cls.old=json.load(stream)

    def schema(self,prefix,version=1):
        task=copy.deepcopy(self.old);task['source_extraction_id_choices_version']=version
        task['reasoning_state']['source_extraction_batches']['active']={'id_prefix':prefix}
        task['input_hash']=input_fingerprint(task)
        views=task['reasoning_state']['reading']['bundle']['views'][:1]
        return original_span_output_schema(task,views)

    def test_actual_batch_has_explicit_unique_ids_and_no_unbound_semantic_suffix(self):
        schema=self.schema('batch2_')
        fields=[v['properties']['id'] for v in schema['properties']['selections']['items']['anyOf']]
        self.assertTrue(fields)
        for field in fields:
            self.assertEqual(len(field['enum']),256)
            self.assertEqual(len(set(field['enum'])),256)
            self.assertTrue(_matches('batch2_item1',field))
            self.assertFalse(_matches('batch1_item1',field))
            self.assertFalse(_matches('batch2_management_fee',field))
            self.assertFalse(_matches('batch2_item257',field))

    def test_batch_spaces_do_not_overlap_and_old_pattern_is_retained(self):
        first=self.schema('batch1_');second=self.schema('batch2_')
        ids=lambda s:set(s['properties']['selections']['items']['anyOf'][0]['properties']['id']['enum'])
        self.assertFalse(ids(first).intersection(ids(second)))
        old=self.schema('batch2_',version=0)['properties']['selections']['items']['anyOf'][0]['properties']['id']
        self.assertIn('pattern',old)
        self.assertTrue(_matches('batch2_management_fee',old))


if __name__=='__main__':unittest.main()
