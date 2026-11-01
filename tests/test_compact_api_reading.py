"""Compact cell references are decoded, never replaced by guessed dates."""
import copy
import unittest

try:
    from . import test_sse_nav_delivery_chain as fixtures
except ImportError:
    import test_sse_nav_delivery_chain as fixtures

from researchhelper.contracts import ContractError
from researchhelper.source_reader import validate_read_specs,read,binding


class CompactAPIReadingTests(unittest.TestCase):
    def setUp(self):
        self.control=fixtures.SSENavDeliveryChainTests();self.control.setUp()
        self.addCleanup(self.control.doCleanups)
        self.task=self.control.task;self.specs=self.control.selections()
        self.bundle=self.control.reading_bundle()

    def validate(self,specs=None,task=None,bundle=None):
        return validate_read_specs(task or self.task,self.specs if specs is None else specs,
            bundle or self.bundle)

    def test_valid_compact_api_cells_pass_without_mutation_or_publication_guess(self):
        before=copy.deepcopy((self.task,self.specs,self.bundle))
        self.validate()
        self.assertEqual((self.task,self.specs,self.bundle),before)
        self.assertTrue(all('period' not in s and s['published_at'] is None for s in self.specs))
        self.assertEqual(self.task['evidence'],[]);self.assertEqual(self.task['calculations'],[])

    def test_missing_reference_does_not_get_a_default_observation_date(self):
        specs=copy.deepcopy(self.specs);specs[0].pop('api_value_reference')
        with self.assertRaises(ContractError):self.validate(specs)

    def test_explicit_period_cannot_override_the_compact_original(self):
        specs=copy.deepcopy(self.specs);specs[0]['period']='2026-10-20'
        with self.assertRaises(ContractError):self.validate(specs)

    def test_changed_snapshot_hash_is_rejected(self):
        specs=copy.deepcopy(self.specs);specs[0]['api_value_reference']['snapshot_hash']='0'*64
        with self.assertRaises(ContractError):self.validate(specs)

    def test_unread_api_is_not_resolved_from_the_wider_task(self):
        ids=[self.control.control.nav['id']]
        ids.extend(x['id'] for x in self.task['materials'])
        bundle=read(self.task,self.bundle['selection'],binding(self.task),ids)
        with self.assertRaises(ContractError):self.validate(bundle=bundle)

    def test_stale_reading_still_fails_before_reference_expansion(self):
        bundle=copy.deepcopy(self.bundle);bundle['binding']['input_hash']='stale'
        with self.assertRaises(ContractError):self.validate(bundle=bundle)

    def test_current_snapshot_does_not_become_historical_permission(self):
        task=copy.deepcopy(self.task);task['research_mode']='historical'
        with self.assertRaises(ContractError):self.validate(task=task)

    def test_foreign_task_source_is_not_accepted(self):
        task=copy.deepcopy(self.task);task['source_records'][0]['task_id']='foreign'
        with self.assertRaises(ContractError):self.validate(task=task)


if __name__=='__main__':unittest.main()
