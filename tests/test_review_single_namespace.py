"""Only opaque review addresses change; financial inputs and guards do not."""
import copy
import json
import unittest
from unittest.mock import patch

from tests import ROOT
from tests import test_review_basis_aliases as fixtures
from researchhelper import argument_support as review
from researchhelper.contracts import ContractError


class ReviewSingleNamespaceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReviewBasisAliasTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = copy.deepcopy(self.fixture.task)
        self.task['review_basis_presentation_version'] = 1
        self.proposal = copy.deepcopy(self.fixture.proposal)
        self.request = review.review_request(self.task, self.proposal)
        self.aliases = {value: key for key, value in review.reference_basis_choices(self.request).items()}
        self.messages = review.review_messages(self.request, use_reference_units=True)
        self.view = json.loads(self.messages[1]['content'])

    def test_source_and_target_edges_use_the_same_namespace(self):
        for name, kind in (('evidence', 'evidence'), ('calculations', 'calculation'), ('knowledge', 'knowledge')):
            for original, actual in zip(self.request[name], self.view[name]):
                self.assertEqual(actual['id'], self.aliases[kind + ':' + original['id']])
                self.assertEqual(actual['basis_ref'], actual['id'])
        for target, shown in zip(self.request['targets'], self.view['targets']):
            self.assertEqual(shown['id'], target['id'])
            self.assertEqual(shown['evidence_refs'], [self.aliases['evidence:' + ref] for ref in target['evidence_refs']])
            self.assertEqual(shown['calculation_refs'], [self.aliases['calculation:' + ref] for ref in target['calculation_refs']])
            self.assertEqual(''.join(unit['text'] for unit in shown['units']), target['text'])

    def test_every_non_address_fact_and_calculation_field_is_preserved(self):
        for name in ('evidence', 'calculations', 'knowledge'):
            for original, shown in zip(self.request[name], self.view[name]):
                for key, value in original.items():
                    if key not in ('id', 'input_refs'):
                        self.assertEqual(shown[key], value, (name, key))
        for original, shown in zip(self.request['calculations'], self.view['calculations']):
            self.assertEqual(shown['input_refs'], [self.aliases['evidence:' + ref] for ref in original['input_refs']])
            # In this engine inputs contains raw financial numbers, not IDs.
            self.assertEqual(shown.get('inputs'), original.get('inputs'))

    def test_proposal_numeric_quotes_and_refs_resolve_to_the_original_graph(self):
        for ident, original in self.request['proposal']['answers'].items():
            shown = self.view['proposal']['answers'][ident]
            self.assertEqual(shown.get('refs'), [self.aliases['evidence:' + ref] for ref in original.get('refs', [])])
            for field, segments in original.get('numeric_segments', {}).items():
                for original_segment, segment in zip(segments, shown['numeric_segments'][field]):
                    expected = copy.deepcopy(original_segment)
                    if 'quote_ref' in expected:
                        expected['quote_ref'] = self.aliases['evidence:' + expected['quote_ref']]
                    elif expected.get('source') in ('evidence', 'calculation'):
                        expected['ref'] = self.aliases[expected['source'] + ':' + expected['ref']]
                    self.assertEqual(segment, expected)

    def test_same_registered_wire_decodes_to_same_canonical_review(self):
        wire = copy.deepcopy(self.fixture.wire)
        wire['binding_ref'] = self.request['review_hash'][:16]
        expected = self.fixture.decode()
        actual = review.decode_reference_review(wire, review.reference_review_schema(self.request), self.request)
        self.assertEqual(actual['assessments'], expected['assessments'])
        self.assertEqual(actual['question_assessments'], expected['question_assessments'])
        self.assertFalse(actual['source_approval'])

    def test_real_transport_uses_unified_prompt_and_one_mock_call(self):
        service = self.fixture.fixture.service()
        self.task['ledger']['used']['model_calls'] = 0  # Private fixture, never a real research ledger.
        wire = copy.deepcopy(self.fixture.wire)
        wire['binding_ref'] = self.request['review_hash'][:16]
        reply = dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message', role='assistant', status='completed',
                         content=[dict(type='output_text', text=json.dumps(wire))])], usage={})
        with patch.object(service, '_request_bytes', return_value=json.dumps(reply).encode()) as transport:
            actual = json.loads(service.reason_argument_support(self.task, self.proposal))
        expected = review.decode_reference_review(wire, review.reference_review_schema(self.request), self.request)
        self.assertEqual(actual, expected)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 1)
        body = json.loads(transport.call_args.args[1].data)
        content = next(item for item in body['input'] if item['role'] == 'user')['content']
        shown = json.loads(content)
        self.assertTrue(all(item['id'] == item['basis_ref'] for item in shown['evidence']))

    def test_old_binding_and_unknown_raw_or_uncited_references_still_reject(self):
        schema = review.reference_review_schema(self.request)
        wire = copy.deepcopy(self.fixture.wire)
        with self.assertRaises(ContractError):
            review.decode_reference_review(wire, schema, self.request)
        wire['binding_ref'] = self.request['review_hash'][:16]
        for ref in ('unknown', 'scope:task', self.request['evidence'][0]['id']):
            changed = copy.deepcopy(wire)
            changed['reviews'][0]['segments'][0]['basis_refs'] = [ref]
            with self.assertRaises(ContractError):
                review.decode_reference_review(changed, schema, self.request)

    def test_original_request_proposal_and_old_prompt_are_not_upgraded(self):
        request, proposal, task = copy.deepcopy((self.request, self.proposal, self.task))
        review.review_messages(self.request, use_reference_units=True)
        self.assertEqual((self.request, self.proposal, self.task), (request, proposal, task))
        old_request = self.fixture.request
        old = json.loads(review.review_messages(old_request, use_reference_units=True)[1]['content'])
        self.assertEqual(old['proposal'], old_request['proposal'])
        self.assertEqual([entry['id'] for entry in old['evidence']], [entry['id'] for entry in old_request['evidence']])

    def test_reference_diagnostic_classifies_without_retaining_rejected_values(self):
        from researchhelper.output_schema import StructuredOutputError
        schema = review.reference_review_schema(self.request)
        for ref, code in [('scope:task', 'canonical_reference_in_alias_field'),
                          (self.request['evidence'][0]['id'], 'canonical_id_in_alias_field'),
                          ('PRIVATE_UNREGISTERED_REJECTED_REFERENCE', 'unknown_basis_reference'),
                          (12345, 'reference_type_mismatch')]:
            wire = copy.deepcopy(self.fixture.wire)
            wire['binding_ref'] = self.request['review_hash'][:16]
            wire['reviews'][0]['segments'][0]['basis_refs'] = [ref]
            with self.assertRaises(StructuredOutputError) as caught:
                review.decode_reference_review(wire, schema, self.request)
            diagnostic = caught.exception.diagnostic
            self.assertEqual(diagnostic['reference_issues'][0]['code'], code)
            self.assertEqual(set(diagnostic['reference_issues'][0]), {'code', 'target_index', 'segment_index'})
            self.assertFalse(diagnostic['submitted_values_retained'])
            self.assertNotIn(str(ref), json.dumps(diagnostic))

    def test_real_failed_macro_view_retains_complete_text_and_all_fact_fields(self):
        path = ROOT / 'result/acceptance/one-sentence-research-sh2kufwn/task.json'
        original = path.read_bytes()
        task = json.loads(original)
        task['review_basis_presentation_version'] = 1
        request = review.review_request(task, task['host_result'])
        view = json.loads(review.review_messages(request, use_reference_units=True)[1]['content'])
        self.assertEqual(len(view['evidence']), 25)
        for before, after in zip(request['evidence'], view['evidence']):
            for key, value in before.items():
                if key != 'id':
                    self.assertEqual(after[key], value)
        self.assertEqual(path.read_bytes(), original)
