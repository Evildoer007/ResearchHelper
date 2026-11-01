"""Replay the retained R115 fee proposal without any external call or approval.

The original selection names both literal dates. Its producer must encode that
duration consistently with the shared interval contract, not turn the last day
into a point observation or rewrite the selected number.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from researchhelper.data_requirements import period_matches
from researchhelper.evidence import (validate_current_snapshot,
                                    validate_calculation_context_basis, candidate_evidence)
from researchhelper.reasoning import evidence_contract, validate_model_specs
from researchhelper import output_schema

STUDY = Path(os.environ.get('RESEARCHHELPER_FEE_DURATION_INPUT', str(
    Path(__file__).resolve().parents[1]/'result/acceptance/one-sentence-research-4rb51vz1/task.json')))


class FeeDurationRoundtripTests(unittest.TestCase):
    def setUp(self):
        self.raw = STUDY.read_bytes()
        self.before = hashlib.sha256(self.raw).hexdigest()
        self.task = json.loads(self.raw)
        self.specs = self.task['reasoning_state']['rejected_extraction']['proposed_specs']
        guard = patch('socket.socket.connect', side_effect=AssertionError('No paid replay'))
        guard.start()
        self.addCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start()
            self.addCleanup(worker.stop)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(STUDY.read_bytes()).hexdigest(), self.before)

    def encode(self, item):
        return output_schema._encode_fee_duration(self.task, item)

    def test_all_four_selected_literal_ranges_survive_both_existing_date_gates(self):
        for original in self.specs:
            item = copy.deepcopy(original)
            self.encode(item)
            self.assertTrue(validate_current_snapshot(item, self.task))
            bound = dict(item, task_id=self.task['id'], research_version=self.task['version'])
            validate_calculation_context_basis(bound, self.task['source_records'])
            self.assertTrue(period_matches(item['period'], 'interval'))
            self.assertEqual(item['period'], '2026-01-01/2026-06-30')
            self.assertEqual(item['calculation_context']['period'], item['period'])
            self.assertEqual(item['reporting_period_basis'], item['calculation_context_basis']['period'])
            for key in ('value', 'raw_value', 'raw_token', 'unit', 'raw_unit', 'text',
                        'artifact_id', 'observation_date', 'published_at', 'review'):
                self.assertEqual(item[key], original[key])
            self.assertNotIn('source_check', item)
            self.assertNotIn('applicability_check', item)

    def test_real_fee_protocol_producer_preserves_all_four_source_and_need_links(self):
        views = self.task['reasoning_state']['reading']['bundle']['views']
        prompt = output_schema.fee_original_prompt(dict(untrusted_sources=views), self.task)
        schema = output_schema.fee_original_output_schema(self.task, views)
        requirements = evidence_contract(self.task, views)['question_requirements']
        sources = []
        for source in prompt['source_choices']:
            rates = []
            options = source['fee_candidates']
            for original in self.specs:
                if original['object'] != source['object_binding']['value']:
                    continue
                context = original['calculation_context']
                rate = next(row for row in options['rates'] if row['metric'] == context['metric'])
                period = next(row for row in options['periods'] if row['value'] == context['period'])
                fund = next(row for row in options['funds'] if row['value'] == context['fund_id'])
                share = next(row for row in options['shares'] if row['value'] == context['share_class'])
                links = [dict(question_id=question, need_ids=[need['id'] for need in requirements[question]
                    if need['text'] in texts]) for question, texts in original['review']['evidence_need_texts'].items()]
                rates.append(dict(id=original['id'], object=original['object'],
                    rate_reference=rate['reference'], period_reference=period['reference'],
                    fund_reference=fund['reference'], share_reference=share['reference'],
                    question_ids=original['question_ids'], need_links=links))
            sources.append(dict(source_ref=source['source_ref'], rates=rates))
        wire = dict(version=2, binding_ref=prompt['fee_binding_ref'], sources=sources)
        decoded = output_schema.decode_fee_original_output(wire, schema, (self.task, views))
        self.assertEqual(len(decoded['evidence_specs']), 4)
        validate_model_specs(self.task, decoded['evidence_specs'])
        for item in decoded['evidence_specs']:
            record = next(row for row in self.task['source_records'] if row['id'] == item['artifact_id'])
            candidate = candidate_evidence(self.task, record, item)
            self.assertEqual(candidate['source_check'], '通过')
            self.assertEqual(candidate['applicability_check'], '待核验')

    def test_no_inference_from_names_wrong_endpoint_or_unrelated_basis(self):
        for change in ('name', 'end', 'part', 'period', 'point'):
            item = copy.deepcopy(self.specs[0])
            if change == 'name':
                item['period'] = item['calculation_context']['period'] = '2026年中期'
            elif change == 'end':
                item['observation_date'] = '2026-01-01'
            elif change == 'part':
                item['reporting_period_basis']['part'] = '不存在的位置'
            elif change == 'period':
                item['period'] = item['calculation_context']['period'] = '2026年1月1日至2026年9月30日'
            else:
                item['period'] = item['calculation_context']['period'] = '2026-06-30'
            before = copy.deepcopy(item)
            self.encode(item)
            self.assertEqual(item, before)
            with self.assertRaises(ContractError):
                validate_current_snapshot(item, self.task)

    def test_legacy_task_does_not_silently_gain_duration_semantics(self):
        self.task.pop('original_duration_anchor_version')
        item = copy.deepcopy(self.specs[0])
        before = copy.deepcopy(item)
        self.encode(item)
        self.assertEqual(item, before)


if __name__ == '__main__':
    unittest.main()
