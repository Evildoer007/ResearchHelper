"""Observation dates and literal reporting periods have separate evidence."""
import copy
import importlib
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint
from tests.test_financial_value_reference import archived_financial_original
from researchhelper.evidence import validate_current_snapshot
from researchhelper.output_schema import (financial_reference_output_schema, financial_reference_prompt,
    financial_reporting_period_choices, decode_financial_references)
fixtures = importlib.import_module((__package__+'.' if __package__ else '')+'test_financial_reference_protocol')

def retained_period_proposal():
    fixture, _ = archived_financial_original()
    task, bundle = copy.deepcopy((fixture['task'], fixture['reading']))
    originals = fixture['host_proposal']['evidence_specs']
    excerpts = copy.deepcopy([item for item in originals if not item.get('calculation_context')][:6])
    for excerpt in excerpts:
        excerpt['period'] = '2026年1-6月'
    choice = next(item for item in financial_reporting_period_choices(task, bundle['views'])
                  if item['period'] == '2026年半年度')
    return task, bundle, excerpts, copy.deepcopy(fixture['financial_specs']), choice


class FinancialTextPeriodReferenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FinancialReferenceProtocolTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = copy.deepcopy(self.fixture.task)
        self.task['financial_text_period_version'] = 2
        self.views = self.fixture.views
        self.schema = financial_reference_output_schema(self.task, self.views)
        self.prompt = financial_reference_prompt(self.fixture.payload, self.task)
        self.choices = financial_reporting_period_choices(self.task, self.views)
        self.wire = copy.deepcopy(self.fixture.wire)
        self.wire['version'] = 9
        self.wire['source_reference'] = self.prompt['financial_reference_binding']
        for item in self.wire['text_quotes']:
            item.pop('period')
            item['period_reference'] = self.choices[0]['reference']

    def decode(self):
        return decode_financial_references(self.wire, self.schema, (self.task, self.views))

    def quote(self):
        return next(item for item in self.decode()['evidence_specs'] if item['id']=='quote_probe')

    def test_interval_is_explicitly_selected_from_actual_header_and_locator(self):
        item = self.quote()
        choice = self.choices[0]
        self.assertEqual(item['period'], choice['period'])
        self.assertEqual(item['reporting_period_basis'], choice['basis'])
        validate_current_snapshot(item, self.task)

    def test_observation_choice_does_not_infer_a_reporting_interval(self):
        self.wire['text_quotes'][0]['period_reference'] = 'observation_date'
        item = self.quote()
        self.assertEqual(item['period'], item['observation_date'])
        self.assertNotIn('reporting_period_basis', item)
        validate_current_snapshot(item, self.task)

    def test_free_period_paraphrase_is_not_an_alternative_input(self):
        self.wire['text_quotes'][0]['period'] = '2026年1-6月'
        with self.assertRaises(ContractError): self.decode()

    def test_unknown_or_missing_period_reference_is_not_repaired(self):
        for value in ('period_999', None):
            with self.subTest(value=value):
                wire = copy.deepcopy(self.wire)
                if value is None: wire['text_quotes'][0].pop('period_reference')
                else: wire['text_quotes'][0]['period_reference'] = value
                with self.assertRaises(ContractError):
                    decode_financial_references(wire, self.schema, (self.task, self.views))

    def test_foreign_snapshot_or_original_is_rejected(self):
        for field in ('artifact_id', 'snapshot_hash'):
            with self.subTest(field=field):
                item = self.quote()
                item['reporting_period_basis'][field] = 'foreign'
                with self.assertRaises(ContractError): validate_current_snapshot(item, self.task)

    def test_period_change_or_forged_quote_is_rejected(self):
        for field, value in (('period', '2026年第二季度'), ('quote', '不存在的原文期间')):
            with self.subTest(field=field):
                item = self.quote()
                if field=='period': item['period'] = value
                else: item['reporting_period_basis'][field] = value
                with self.assertRaises(ContractError): validate_current_snapshot(item, self.task)

    def test_bad_declared_basis_cannot_hide_behind_a_literal_period_elsewhere(self):
        item = self.quote()
        item['reporting_period_basis']['part'] = '不存在的页'
        with self.assertRaises(ContractError): validate_current_snapshot(item, self.task)

    def test_reporting_basis_cannot_be_attached_without_current_original_proof(self):
        item = self.quote()
        item.pop('availability')
        with self.assertRaises(ContractError): validate_current_snapshot(item, self.task)

    def test_legacy_protocol_keeps_its_original_period_field(self):
        schema = financial_reference_output_schema(self.fixture.task, self.views)
        self.assertEqual(schema['properties']['version']['enum'], [8])
        self.assertIn('period', schema['properties']['text_quotes']['items']['properties'])
        self.assertNotIn('period_reference', schema['properties']['text_quotes']['items']['properties'])

    def test_binding_cannot_be_reused_for_other_input_or_period_choices(self):
        for field, value in (('id','foreign'), ('input_hash','foreign')):
            task = copy.deepcopy(self.task)
            task[field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                decode_financial_references(self.wire, self.schema, (task, self.views))

    def test_decoding_does_not_approve_evidence_or_modify_source_task(self):
        before = copy.deepcopy((self.task, self.views, self.wire))
        self.decode()
        self.assertEqual((self.task, self.views, self.wire), before)
        self.assertFalse(self.task['evidence'])

    def test_duplicate_ids_cannot_replace_a_different_records_period_basis(self):
        self.wire['text_quotes'][0]['id'] = self.wire['financial_cells'][0]['id']
        with self.assertRaises(ContractError): self.decode()

    def test_unlocated_free_form_period_is_rejected_on_retained_original(self):
        from researchhelper.reasoning import validate_model_specs

        task, _, excerpts, amounts, _ = retained_period_proposal()
        before = fingerprint(task)
        with self.assertRaisesRegex(ContractError, '财务所属期间'):
            validate_model_specs(task, amounts + excerpts)
        self.assertEqual(fingerprint(task), before)

    def test_explicit_header_choice_preserves_original_amounts_and_uses_read_sources(self):
        from researchhelper.reasoning import validate_model_specs
        from researchhelper.source_reader import validate_read_specs

        task, bundle, excerpts, amounts, choice = retained_period_proposal()
        before = fingerprint((task, bundle, amounts))
        self.assertEqual(len(excerpts), 6)
        for excerpt in excerpts:
            excerpt['period'] = choice['period']
            excerpt['reporting_period_basis'] = copy.deepcopy(choice['basis'])
        validate_model_specs(task, amounts + excerpts)
        validate_read_specs(task, amounts + excerpts, bundle)
        self.assertFalse(task['evidence'])
        self.assertEqual(fingerprint((task, bundle, amounts)), before)

    def test_unread_period_basis_is_rejected_even_when_the_original_quote_exists(self):
        from researchhelper.source_reader import validate_read_specs, ReadingAnchorError

        task, bundle, excerpts, _, choice = retained_period_proposal()
        before = fingerprint(task)
        spec = excerpts[0]
        spec['period'] = choice['period']
        spec['reporting_period_basis'] = copy.deepcopy(choice['basis'])
        self.assertNotIn(choice['basis']['part'],
                         (spec['record_locator']['part'], spec['record_locator']['period_part']))
        for view in bundle['views']:
            if view['id'] == choice['basis']['artifact_id']:
                view['parts'] = [part for part in view['parts'] if part['locator'] != choice['basis']['part']]
        # Exercise the anchor check separately from the earlier reading-signature check.
        with patch('researchhelper.source_reader.validate_reading'):
            with self.assertRaises(ReadingAnchorError) as error:
                validate_read_specs(task, [spec], bundle)
        self.assertEqual(error.exception.diagnostic['field'], 'reporting_period_basis')
        self.assertEqual(fingerprint(task), before)

    def test_multi_original_prompt_keeps_each_sources_period_choices_and_version(self):
        batch = importlib.import_module((__package__+'.' if __package__ else '')+'test_original_batch_protocol')
        from researchhelper.output_schema import original_batch_output_schema, original_batch_prompt, decode_original_batch
        fixture = batch.OriginalBatchProtocolTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        task = copy.deepcopy(fixture.task)
        task['financial_text_period_version'] = 2
        schema = original_batch_output_schema(task, fixture.views)
        prompt = original_batch_prompt(fixture.payload, task)
        choices = {item['source_ref']:item for item in prompt['source_choices']}
        wire = copy.deepcopy(fixture.wire)
        wire['binding_ref'] = prompt['batch_binding_ref']
        for item in wire['sources']:
            choice = choices[item['source_ref']]
            if choice['selection_kind']=='financial':
                self.assertTrue(choice['reporting_period_choices'])
                item['selection']['version'] = 9
                item['selection']['source_reference'] = choice['binding']
            else:
                self.assertNotIn('reporting_period_choices', choice)
        decoded = decode_original_batch(wire, schema, (task, fixture.views))
        self.assertTrue(decoded['evidence_specs'])


if __name__=='__main__':
    unittest.main()
