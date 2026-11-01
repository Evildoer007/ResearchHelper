"""Independent, offline lossless-model-input checks; no existing study rerun."""
import copy
import gzip
import hashlib
import json
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import ContractError, canonical
from researchhelper.engine import ResearchAgent
from researchhelper.model_presentation import GUIDE, present, restore
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS


class ModelInputPresentationTests(unittest.TestCase):
    def test_records_roundtrip_all_types_and_no_mutation(self):
        source = [dict(id=str(i), value=i / 10, raw_value=str(i), nested=dict(
            condition='原单位、观测时点与用途限制保持不变。' * 20, empty={}, flag=True),
            nullable=None, sequence=[1, False, '1']) for i in range(300)]
        before = copy.deepcopy(source)
        encoded, changed = present(source)
        self.assertTrue(changed)
        self.assertEqual(canonical(restore(encoded)), canonical(source))
        self.assertEqual(source, before)
        self.assertEqual(len(restore(encoded)), 300)

    def test_reserved_keys_and_missing_are_not_confused(self):
        source = dict(records=[dict(id=str(i), constant='条件' * 100, value={'$rh_ref': i})
                               for i in range(30)],
                      other=[{'other': None}, {'other': False}, {'other': 1}, {},
                             {'$rh_literal': [1]}, {'$rh_table': {'x': 1}}])
        encoded, changed = present(source)
        self.assertTrue(changed)
        self.assertEqual(canonical(restore(encoded)), canonical(source))

    def test_identical_large_values_shared_but_types_distinct(self):
        same = [str(i) * 10 for i in range(100)]
        source = dict(first=same, second=copy.deepcopy(same), flags=[{'value': x, 'text': '限制' * 100}
                     for x in [True, 1, 1.0, False, 0, 0.0, None, '1']])
        encoded, changed = present(source)
        self.assertTrue(changed)
        self.assertTrue(encoded['shared'])
        self.assertEqual(canonical(restore(encoded)), canonical(source))

    def test_small_payload_stays_ordinary_and_bad_reference_rejected(self):
        source = dict(evidence=[], gaps=['仍缺必要数据'])
        result, changed = present(source)
        self.assertFalse(changed)
        self.assertEqual(result, source)
        for index in (True, -1, 1, '0'):
            with self.subTest(index=index), self.assertRaises(ContractError):
                restore(dict(format='researchhelper.table_input', version=1,
                             shared=[{'$rh_ref': 0}], payload={'$rh_ref': index}))

    def test_actual_index_payload_keeps_every_row_and_fits_existing_limit(self):
        path = ROOT / 'result/acceptance/one-sentence-research-jftzzkjp/task.json'
        if not path.exists(): path = path.with_suffix('.json.gz')
        from maintenance.compact_acceptance import digest as file_digest
        compressed = path.suffix == '.gz'
        digest = file_digest(path, compressed=compressed)
        with (gzip.open(path, 'rt', encoding='utf-8') if compressed else path.open()) as incoming:
            task = json.load(incoming)
        agent = ResearchAgent.__new__(ResearchAgent)
        captured = {}
        class Captured(Exception): pass
        def capture(model, current, phase, payload, fields, **kwargs):
            self.assertEqual(phase, 'analyze')
            captured.update(payload)
            raise Captured()
        agent.model = object()
        with patch('socket.socket.connect', side_effect=AssertionError('Offline only')), \
                patch('researchhelper.engine.model_object', side_effect=capture):
            with self.assertRaises(Captured):
                agent._reason_on_verified_material(task)
        encoded, changed = present(captured)
        self.assertTrue(changed)
        self.assertEqual(canonical(restore(encoded)), canonical(captured))
        self.assertEqual(len(restore(encoded)['evidence']), 300)
        self.assertLess(len(canonical(encoded)) + len(GUIDE), MAX_CONTEXT_CHARACTERS)
        from researchhelper.output_schema import analysis_output_schema, analysis_protocol, provider_request_schema
        task['analysis_input_presentation_version'] = 1
        schema = analysis_output_schema(task, [q['id'] for q in captured['questions']])
        protocol, _ = analysis_protocol(task)
        schema_size = len(canonical(provider_request_schema(task, schema, protocol)))
        self.assertLess(len(canonical(encoded)) + len(GUIDE) + schema_size, MAX_CONTEXT_CHARACTERS)
        # Actual model_object boundary, stopping before any network dispatch.
        from researchhelper.reasoning import model_object, analysis_fields
        class Dispatched(Exception): pass
        class Observer:
            def supports_structured_analysis(self): return True
            def reason_structured_phase(self, current, messages, phase, schema):
                self.messages = messages
                raise Dispatched()
        observer = Observer()
        with patch('socket.socket.connect', side_effect=AssertionError('Offline only')):
            with self.assertRaises(Dispatched):
                model_object(observer, task, 'analyze', captured, analysis_fields(task))
        self.assertEqual(canonical(restore(json.loads(observer.messages[1]['content']))), canonical(captured))
        # The independent reviewer must measure its actual, complete input,
        # not reject the unshared canonical copy before presentation.
        from researchhelper import argument_support as support
        task['analysis_input_presentation_version'] = 2
        from researchhelper.planning import input_fingerprint
        # Detached grammar comparison only, not a new adopted study. Never
        # write this copy, rebind source receipts or resume the retained task.
        task['input_hash'] = input_fingerprint(task)
        proposal = dict(answers={task['questions'][0]['id']: dict(
            answer='按本次原件核对，完整明细保留在研究底稿。',
            refs=[e['id'] for e in task['evidence']], calculation_refs=[])}, theses=[])
        request = support.review_request(task, proposal)
        self.assertGreater(len(canonical(request)), support.MAX_CHARACTERS)
        reviewed = support.review_messages(request, use_reference_units=True)
        self.assertLess(len(reviewed[1]['content']), support.MAX_CHARACTERS)
        restored = restore(json.loads(reviewed[1]['content']))
        self.assertEqual(len(restored['evidence']), 300)
        aliases = support.reference_basis_choices(request)
        actual_ids = {aliases[e['basis_ref']].split(':', 1)[1] for e in restored['evidence']}
        self.assertEqual(actual_ids, {e['id'] for e in task['evidence']})
        original_facts = {e['id']: e for e in request['evidence']}
        for fact in restored['evidence']:
            original = original_facts[aliases[fact['basis_ref']].split(':', 1)[1]]
            for field in ('text', 'period', 'unit', 'value', 'raw_value', 'source_check', 'applicability_check'):
                self.assertEqual(fact.get(field), original.get(field))
        self.assertEqual(file_digest(path, compressed=compressed), digest)
        print('Index input characters:', len(canonical(captured)), '->', len(canonical(encoded)),
              'provider schema:', schema_size, 'review input:', len(reviewed[1]['content']))


if __name__ == '__main__': unittest.main()
