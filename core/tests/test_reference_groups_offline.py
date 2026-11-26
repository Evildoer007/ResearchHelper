"""No citation ID or repeated original can create independent event evidence."""

import copy
import unittest

from researchhelper.contracts import ContractError, validate_evidence
from researchhelper.evidence import candidate_evidence, reference_groups
from researchhelper.reports import compose
from researchhelper.reasoning import analysis_contract
from tests import test_version_review_offline as versions
from tests import test_evidence_chain_offline as chains


class ReferenceGroupsTests(unittest.TestCase):
    setUp = versions.VersionReviewTests.setUp
    complete = versions.VersionReviewTests.complete
    host_result = versions.VersionReviewTests.host_result
    def test_one_original_many_quotes_keep_refs_but_not_multiple_events(self):
        done = self.complete()
        thesis = done['theses'][0]
        self.assertEqual(thesis['support'], ['effective_date', 'eligibility'])
        self.assertEqual(len(thesis['support_source_groups']), 1)
        self.assertEqual(thesis['support_event_groups'], [])
        self.assertEqual(thesis['support_unidentified_event_refs'], thesis['support'])

    def test_renamed_same_bytes_share_carrier_not_event(self):
        task = self.fixture.task(kind='mechanism')
        original = task['materials'][0]
        task = self.agent.add_material(task['id'], 'renamed.txt', versions.fixtures.TEXT.encode())
        self.assertEqual(len(task['materials']), 1)
        self.assertEqual(task['materials'][0], original)
        # Distinct citations still reference the same retained carrier.
        evidence = {'0': {'artifact': original}, '1': {'artifact': task['materials'][0]}}
        groups = reference_groups(evidence, ['0', '1', '0'])
        self.assertEqual(len(groups['source_groups']), 1)
        self.assertEqual(groups['event_groups'], [])
        self.assertEqual(groups['unidentified_event_refs'], ['0', '1'])

    def test_different_carriers_do_not_assert_independent_confirmation(self):
        task = self.fixture.task()
        task = self.agent.add_material(task['id'], 'another.txt', (versions.fixtures.TEXT + '\n另一份材料。').encode())
        evidence = {str(i): {'artifact': record} for i, record in enumerate(task['materials'])}
        self.assertEqual(len(reference_groups(evidence, ['0', '1'])['source_groups']), 2)
        self.assertEqual(reference_groups(evidence, ['0', '1'])['event_groups'], [])
        self.assertIn('不证明独立确认', analysis_contract(task)['reference_group_rule'])

    def test_unregistered_carrier_and_event_are_explicitly_unknown(self):
        groups = reference_groups({'a': {'id': 'a', 'event_group': 'invented'}}, ['a'])
        self.assertEqual(groups['source_groups'], [])
        self.assertEqual(groups['event_groups'], [])
        self.assertEqual(groups['unidentified_source_refs'], ['a'])
        self.assertEqual(groups['unidentified_event_refs'], ['a'])

    def test_host_event_label_is_removed_not_adopted(self):
        task = self.fixture.task(kind='mechanism')
        spec = dict(self.fixture.specs(task)[0], event_group='invented_event')
        item = candidate_evidence(task, task['materials'][0], spec)
        self.assertNotIn('event_group', item)
        done = self.complete()
        corrupted = dict(done['evidence'][0], event_group='invented_event')
        with self.assertRaises(ContractError):
            validate_evidence(corrupted, done)

    def test_report_rechecks_every_source_event_and_unknown_ref_field(self):
        done = self.complete()
        for field in ('support_source_groups', 'support_event_groups', 'support_unidentified_event_refs',
                      'support_unidentified_source_refs', 'counter_source_groups', 'counter_event_groups',
                      'counter_unidentified_event_refs', 'counter_unidentified_source_refs'):
            corrupted = copy.deepcopy(done)
            corrupted['theses'][0][field] = ['invented']
            with self.subTest(field=field), self.assertRaises(ContractError):
                compose(corrupted)

    def test_analysis_exposes_readonly_groups_without_changing_evidence(self):
        done = self.complete()
        before = copy.deepcopy(done)
        contract = analysis_contract(done)
        groups = contract['by_question']['q1']['reference_groups']
        self.assertEqual(groups['source_groups'], done['theses'][0]['support_source_groups'])
        self.assertEqual(groups['unidentified_event_refs'], ['effective_date', 'eligibility'])
        self.assertEqual(done, before)

    def test_retained_repost_event_is_preserved_and_shared(self):
        fixture = chains.EvidenceChainTests()
        fixture.setUp()
        try:
            first, _ = fixture.filing()
            second, _ = fixture.filing(fixture.directory(source='另一转载渠道', source_id='repost-002', original_url='https://example.invalid/repost.txt'))
            refs = {'a': {'artifact': first, 'event_group': first['event_group']},
                    'b': {'artifact': second, 'event_group': second['event_group']}}
            groups = reference_groups(refs, ['a', 'b'])
            self.assertEqual(groups['event_groups'], [first['event_group']])
            self.assertEqual(len(groups['source_groups']), 1)
            self.assertEqual(groups['unidentified_event_refs'], [])
        finally:
            fixture.doCleanups()
