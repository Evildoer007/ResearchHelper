"""Explicit grouped API wire is equivalent to canonical flat evidence proposals.

Local synthetic SDK fixture only. This tests representation, not live provider
permission or independent observations; repeated selections never create facts.
"""
import copy
import json
import unittest
from unittest.mock import patch

from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError, fingerprint, canonical
from researchhelper.evidence import API_VALUE_FIELDS, api_value_reference, candidate_evidence
from researchhelper.reasoning import expand_api_evidence_groups, evidence_contract, model_object, validate_model_specs
from tests import test_current_snapshot_availability_offline as acquired


class GroupedAPIProposalTests(unittest.TestCase):
    def setUp(self):
        fixture = acquired.CurrentSnapshotAvailabilityTests()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.fixture = fixture; self.task, self.record = fixture.acquired()
        self.proposal = fixture.spec(self.task, self.record)
        for key in API_VALUE_FIELDS | {'raw_token'}: self.proposal.pop(key, None)
        self.proposal['api_value_reference'] = api_value_reference(self.record)

    def group(self, count=1):
        shared = {key: copy.deepcopy(value) for key, value in self.proposal.items() if key not in {'id', 'record_locator'}}
        return {'format': 'api_row_group_v1', 'shared': shared,
                'rows': [{'id': 'selection_' + str(index), 'record_locator': copy.deepcopy(self.proposal['record_locator'])}
                         for index in range(count)]}

    def test_group_decodes_to_identical_detached_flat_proposals(self):
        group = self.group(2); before = copy.deepcopy((self.task, group))
        actual = expand_api_evidence_groups(self.task, [group])
        expected = [dict(copy.deepcopy(self.proposal), id='selection_' + str(index)) for index in range(2)]
        self.assertEqual(actual, expected); self.assertEqual((self.task, group), before)
        actual[0]['review']['reason'] = '仅修改返回值'
        self.assertEqual((self.task, group), before)

    def test_group_and_flat_inputs_obtain_identical_two_gate_receipts(self):
        expanded = expand_api_evidence_groups(self.task, [self.group()])[0]
        flat = dict(copy.deepcopy(self.proposal), id=expanded['id'])
        self.assertEqual(self.fixture.approved(self.task, self.record, expanded),
                         self.fixture.approved(self.task, self.record, flat))

    def test_repeated_metadata_is_materially_smaller_without_dropping_row_selectors(self):
        group = self.group(14); flat = expand_api_evidence_groups(self.task, [group])
        self.assertLess(len(canonical([group])), len(canonical(flat)) * .3)
        self.assertEqual([item['id'] for item in flat], [row['id'] for row in group['rows']])
        self.assertTrue(all(item['record_locator'] == row['record_locator'] for item, row in zip(flat, group['rows'])))

    def test_host_entry_stores_canonical_evidence_without_extra_model(self):
        payload = {'evidence_specs': [self.group()], 'answers': {'q1': {
            'answer': '本次取得的版本记录收盘价为4.5元。', 'refs': ['selection_0']}}}
        original = copy.deepcopy(payload)
        done = self.fixture.agent.start(self.task['id'], payload)
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertEqual(done['ledger']['used']['data_requests'], 2)
        self.assertNotIn('format', done['host_result']['evidence_specs'][0])
        self.assertEqual(done['evidence'][0]['value'], 4.5); self.assertEqual(payload, original)

    def test_model_entry_records_wire_hash_and_canonical_hash_separately(self):
        group = self.group(); wire = {'evidence_specs': [group]}
        class Model:
            def reason_phase(self, task, messages, phase): return json.dumps(wire)
        value, receipt = model_object(Model(), self.task, 'extract', {}, {'evidence_specs'})
        self.assertEqual(value['evidence_specs'], expand_api_evidence_groups(self.task, [group]))
        self.assertEqual(receipt['api_group_format_version'], 1)
        self.assertEqual(receipt['group_wire_hash'], fingerprint(wire))
        self.assertEqual(receipt['output_hash'], fingerprint(value))

    def test_shared_and_row_overlap_is_never_overridden(self):
        for key in ('artifact_id', 'object', 'review', 'api_value_reference'):
            group = self.group(); group['rows'][0][key] = copy.deepcopy(group['shared'][key])
            with self.subTest(key=key), self.assertRaises(ContractError): expand_api_evidence_groups(self.task, [group])

    def test_group_cannot_sign_approval_or_hide_unknown_fields(self):
        changes = (lambda g: g.update(source_check='通过'),
                   lambda g: g['shared'].update(applicability_check='通过'),
                   lambda g: g['rows'][0].update(snapshot={}),
                   lambda g: g['rows'][0].update(format='api_row_group_v1'))
        for mutate in changes:
            group = self.group(); mutate(group)
            with self.subTest(mutate=mutate), self.assertRaises(ContractError): expand_api_evidence_groups(self.task, [group])

    def test_missing_or_shared_identifiers_and_locators_are_rejected(self):
        for key in ('id', 'record_locator'):
            group = self.group(); value = group['rows'][0].pop(key)
            with self.subTest(key=key), self.assertRaises(ContractError): expand_api_evidence_groups(self.task, [group])
            group['shared'][key] = value
            with self.assertRaises(ContractError): expand_api_evidence_groups(self.task, [group])

    def test_malformed_empty_and_nested_groups_are_rejected(self):
        for rows in (None, {}, [], [None], [self.group()], [{'id': False, 'record_locator': {}}]):
            group = self.group(); group['rows'] = rows
            with self.subTest(rows=rows), self.assertRaises(ContractError): expand_api_evidence_groups(self.task, [group])

    def test_expanded_limits_and_duplicate_ids_include_flat_specs(self):
        group = self.group(1000)
        with self.assertRaises(ContractError): expand_api_evidence_groups(self.task, [group, dict(self.proposal, id='additional')])
        group = self.group(2); group['rows'][1]['id'] = group['rows'][0]['id']
        with self.assertRaises(ContractError): expand_api_evidence_groups(self.task, [group])
        group = self.group()
        with self.assertRaises(ContractError): expand_api_evidence_groups(self.task, [group, dict(self.proposal, id='selection_0')])

    def test_cross_task_version_source_hash_and_history_are_rejected(self):
        for mutate in (
            lambda t: t.update(id='another_task'), lambda t: t.update(version=2),
            lambda t: t.update(research_mode='historical'), lambda t: t.update(intent='原件内容核对'),
            lambda t: t['source_records'][0].update(hash='wrong_hash'),
            lambda t: t['source_records'][0].update(operation='income'),
        ):
            task = copy.deepcopy(self.task); mutate(task)
            with self.subTest(mutate=mutate), self.assertRaises(ContractError): expand_api_evidence_groups(task, [self.group()])
        group = self.group(); group['shared']['api_value_reference']['snapshot_hash'] = 'wrong_hash'
        with self.assertRaises(ContractError): expand_api_evidence_groups(self.task, [group])

    def test_decoder_does_not_grant_missing_review_identity_or_value(self):
        for key in ('review', 'direct_code_reference'):
            group = self.group(); group['shared'].pop(key)
            spec = expand_api_evidence_groups(self.task, [group])[0]
            if key == 'direct_code_reference':
                with self.assertRaises(ContractError): candidate_evidence(self.task, self.record, spec)
            else:
                item = candidate_evidence(self.task, self.record, spec)
                with self.assertRaises(ContractError): ResearchAnalyzer().assess(self.task, item, spec.get('review'), self.task['source_records'])
        spec = expand_api_evidence_groups(self.task, [self.group()])[0]
        for key in API_VALUE_FIELDS: self.assertNotIn(key, spec)
        spec['value'] = 999
        with self.assertRaises(ContractError): validate_model_specs(self.task, [spec], schema_only=True)

    def test_no_network_dispatch_budget_charge_or_source_mutation(self):
        before = copy.deepcopy(self.task)
        with patch('urllib.request.urlopen') as transport: expand_api_evidence_groups(self.task, [self.group()])
        transport.assert_not_called(); self.assertEqual(self.task, before)

    def test_legacy_flat_and_empty_input_are_unchanged(self):
        for original in ([], [self.proposal]):
            self.assertEqual(expand_api_evidence_groups(self.task, original), original)

    def test_contract_advertises_optional_current_scope_not_permission(self):
        contract = evidence_contract(self.task)['api_row_groups']
        self.assertTrue(contract['available']); self.assertFalse(contract['automatic_approval'])
        task = copy.deepcopy(self.task); task['research_mode'] = 'historical'
        self.assertFalse(evidence_contract(task)['api_row_groups']['available'])


if __name__ == '__main__':
    unittest.main()
