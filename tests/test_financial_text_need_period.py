"""Financial quote selectors respect declared value kind and period shape.

The original failed study is immutable. Corrected wires below are forensic
controls, not automatic repairs or a successful real research rerun.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch
from researchhelper.output_schema import (financial_reference_output_schema,
    financial_reference_prompt, decode_financial_references, _matches,
    original_batch_prompt, original_batch_output_schema,
    financial_reporting_period_choices, _financial_text_period_need_schema)
from researchhelper.reasoning import evidence_contract
from researchhelper.contracts import ContractError, fingerprint

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('RESEARCHHELPER_REPORTED_PERIOD_INPUT', str(
    ROOT/'result/acceptance/one-sentence-research-o9nv4a91/task.json')))


class FinancialTextNeedPeriodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = SOURCE.read_bytes()
        cls.original = json.loads(cls.raw)

    def setUp(self):
        self.task = copy.deepcopy(self.original)
        self.task['financial_period_need_compatibility_version'] = 1
        self.views = self.task['reasoning_state']['reading']['bundle']['views']
        self.spec = self.task['host_result']['evidence_specs'][5]
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No network'))
        guard.start(); self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(SOURCE.read_bytes(), self.raw)

    def selection(self, task=None):
        task = self.task if task is None else task
        schema = financial_reference_output_schema(task, self.views)
        prompt = financial_reference_prompt(dict(untrusted_sources=self.views,
            evidence_contract=evidence_contract(task, self.views)), task)
        quote = next(ref for part in prompt['untrusted_sources'][0]['parts']
                     for ref, text in part['quote_choices'] if text == self.spec['text'])
        observation = next(item for item in prompt['untrusted_sources'][0]['observation_choices']
                           if item['date'] == self.spec['observation_date']
                           and item['part'] == self.spec['record_locator']['period_part'])
        need = task['questions'][0]['evidence_needs'][-1]
        item = dict(id='metadata_period_control', object=self.spec['object'], nature=self.spec['nature'],
            quote_reference=quote, observation_reference=observation['reference'],
            period_reference='observation_date', review=dict(scope='current_original_descriptive_only',
                need_links={qid: ([fingerprint(need)] if qid == 'q1' else None)
                            for qid in prompt['evidence_contract']['question_ids']}))
        return schema, prompt, dict(version=9, source_reference=prompt['financial_reference_binding'],
                                   current_version_only=True, financial_cells=[], text_quotes=[item])

    def test_actual_point_interval_pair_is_rejected_before_extraction_adoption(self):
        schema, _, wire = self.selection()
        old = copy.deepcopy(self.task)
        old.pop('financial_period_need_compatibility_version')
        old_schema = financial_reference_output_schema(old, self.views)
        self.assertTrue(_matches(wire, old_schema))
        self.assertFalse(_matches(wire, schema))
        with self.assertRaises(ContractError):
            decode_financial_references(wire, schema, (self.task, self.views))

    def test_explicit_literal_duration_roundtrip_keeps_text_observation_and_source_basis(self):
        schema, prompt, wire = self.selection()
        selected = next(item for item in prompt['reporting_period_choices'] if item['period'] == '2026年半年度')
        wire['text_quotes'][0]['period_reference'] = selected['reference']
        before = copy.deepcopy(wire)
        self.assertTrue(_matches(wire, schema))
        spec = decode_financial_references(wire, schema, (self.task, self.views))['evidence_specs'][0]
        self.assertEqual(spec['period'], '2026年半年度')
        self.assertEqual(spec['observation_date'], self.spec['observation_date'])
        self.assertEqual(spec['text'], self.spec['text'])
        self.assertEqual(spec['reporting_period_basis'], selected['basis'])
        self.assertEqual(spec['review']['evidence_need_texts'], self.spec['review']['evidence_need_texts'])
        self.assertNotIn('source_check', spec)
        self.assertNotIn('applicability_check', spec)
        self.assertEqual(wire, before)

    def test_text_quote_cannot_claim_numeric_need_and_empty_background_links_remain(self):
        schema, prompt, wire = self.selection()
        item = wire['text_quotes'][0]
        item['period_reference'] = prompt['reporting_period_choices'][0]['reference']
        item['review']['need_links']['q1'] = [fingerprint(self.task['questions'][0]['evidence_needs'][0])]
        self.assertFalse(_matches(wire, schema))
        item['review']['need_links']['q1'] = []
        item['period_reference'] = 'observation_date'
        self.assertTrue(_matches(wire, schema))
        spec = decode_financial_references(wire, schema, (self.task, self.views))['evidence_specs'][0]
        self.assertNotIn('evidence_need_texts', spec['review'])
        self.assertEqual(spec['question_ids'], ['q1'])

    def test_mixed_question_periods_keep_their_own_shape_constraints(self):
        # Isolated schema matrix, not a rebind of the real acquired source to
        # another plan. The other tests retain the actual acquisition binding.
        task = copy.deepcopy(self.task)
        question = copy.deepcopy(task['questions'][0])
        text = '格式夹具：原件某项单日文字事实'
        question.update(id='q2', question=text, evidence_needs=[text],
            evidence_need_constraints={text: dict(value_kind='text', unit_dimension='not_applicable',
                                                  minimum_count=1, period_kind='point')})
        task['questions'].append(question)
        _, prompt, wire = self.selection()
        legacy = copy.deepcopy(self.task)
        legacy.pop('financial_period_need_compatibility_version')
        quote = financial_reference_output_schema(legacy, self.views)['properties']['text_quotes']['items']
        mapping = quote['properties']['review']['properties']['need_links']
        mapping['properties']['q2'] = copy.deepcopy(mapping['properties']['q1'])
        mapping['required'].append('q2')
        contract = evidence_contract(self.task, self.views)
        contract['question_requirements']['q2'] = [dict(id=fingerprint(text), text=text)]
        periods = financial_reporting_period_choices(self.task, self.views)
        with patch('researchhelper.reasoning.evidence_contract', return_value=contract), \
             patch('researchhelper.output_schema.financial_reporting_period_choices', return_value=periods):
            schema = _financial_text_period_need_schema(task, self.views, quote)
        item = wire['text_quotes'][0]
        item['review']['need_links'] = dict(q1=None, q2=[fingerprint(text)])
        self.assertTrue(_matches(item, schema))
        item['period_reference'] = prompt['reporting_period_choices'][0]['reference']
        self.assertFalse(_matches(item, schema))
        item['review']['need_links'] = dict(q1=[fingerprint(task['questions'][0]['evidence_needs'][-1])], q2=None)
        self.assertTrue(_matches(item, schema))

    def test_unknown_period_owner_or_version_never_gets_a_repaired_selection(self):
        schema, _, wire = self.selection()
        item = wire['text_quotes'][0]
        item['period_reference'] = 'period_foreign'
        self.assertFalse(_matches(wire, schema))
        task = copy.deepcopy(self.task)
        for version in (0, True, 2):
            task['financial_period_need_compatibility_version'] = version
            with self.subTest(version=version), self.assertRaises(ContractError):
                financial_reference_output_schema(task, self.views)

    def test_batch_exposes_the_same_compatibility_rule_without_changing_original_text(self):
        before = copy.deepcopy(self.views)
        payload = dict(untrusted_sources=self.views, evidence_contract=evidence_contract(self.task, self.views))
        prompt = original_batch_prompt(payload, self.task)
        self.assertTrue(prompt['source_choices'][0]['reporting_period_need_rule'])
        batch = original_batch_output_schema(self.task, self.views)
        nested = batch['properties']['sources']['items']['anyOf'][0]['properties']['selection']
        self.assertEqual(nested, financial_reference_output_schema(self.task, self.views))
        self.assertEqual(self.views, before)


if __name__ == '__main__':
    unittest.main()
