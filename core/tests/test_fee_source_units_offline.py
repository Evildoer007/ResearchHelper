"""Preserve actual source percent units while independently checking frequency."""

import copy
import unittest

from researchhelper.contracts import (ContractError, METHOD_REGISTRY, calculation_input_units_available,
                                      fingerprint, valid_calculation_input_unit, validate_calculation_context)
from researchhelper.knowledge import ENTRIES, RETAINED_VERSIONS, ResearchKnowledge
from researchhelper.reasoning import PHASE_RULES
from tests import test_management_custody_fee_offline as previous

METHOD = previous.METHOD


class FeeSourceUnitsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = previous.ManagementCustodyFeeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent = self.fixture.agent

    def prepare(self, percent_fields=None, frequency='年费率'):
        task, host = self.fixture.prepare()
        chosen = set(previous.VALUES) if percent_fields is None else set(percent_fields)
        for field in chosen:
            item = next(spec for spec in host['evidence_specs'] if spec['id'] == 'ev_' + field)
            text = item['text'].replace('%/年', '%').replace('年费率', frequency)
            task = self.agent.add_material(task['id'], field + '-source-percent.txt', text.encode())
            material = task['materials'][-1]
            item.update(artifact_id=material['id'], text=text, raw_unit='%', unit='%')
            for anchor in [item['publication']['basis_ref'], *item['calculation_context_basis'].values()]:
                anchor.update(artifact_id=material['id'], snapshot_hash=material['snapshot_hash'], quote=text)
        return task, host

    def test_percent_originals_deliver_without_rewriting_or_annualizing(self):
        task, host = self.prepare()
        final = self.agent.start(task['id'], host)
        self.assertEqual(final['status'], '已完成', final.get('error'))
        self.assertEqual(len(final['evidence']), 4)
        for evidence in final['evidence']:
            self.assertEqual(evidence['raw_unit'], '%')
            self.assertEqual(evidence['unit'], '%')
            self.assertEqual(evidence['raw_value'], evidence['value'])
            self.assertEqual(evidence['conversion_factor'], 1)
        calculation = final['calculations'][0]
        self.assertAlmostEqual(calculation['output']['value'], -.4)
        self.assertEqual(calculation['output']['unit'], '百分点/年')
        self.assertEqual(calculation['method_binding']['knowledge_version'], 2)
        self.assertEqual(calculation['implementation_version'], 1)
        self.assertEqual((final['ledger']['used']['model_calls'], final['ledger']['used']['data_requests']), (0, 0))
        report = self.agent.export(task['id'])
        self.assertIn('0.15%</td>', report['html'])
        self.assertNotIn('0.15%/年</td>', report['html'])
        self.assertEqual(self.agent.archive.get(task['id'], 1)['calculations'], final['calculations'])

    def test_mixed_registered_representations_keep_each_original_unit(self):
        task, host = self.prepare({'management_a', 'custody_b'})
        final = self.agent.start(task['id'], host)
        self.assertEqual(final['status'], '已完成', final.get('error'))
        self.assertAlmostEqual(final['calculations'][0]['output']['value'], -.4)
        page = self.agent.export(task['id'])['html']
        for value in ('0.15%</td>', '0.05%/年</td>', '0.5%/年</td>', '0.1%</td>'):
            self.assertIn(value, page)

    def test_percent_without_annual_source_is_not_approved(self):
        for frequency in ('月费率', '费率'):
            task, host = self.prepare(frequency=frequency)
            final = self.agent.start(task['id'], host)
            self.assertEqual(final['status'], '部分完成')
            self.assertFalse(final['calculations'])
            self.assertTrue(final['rejected_evidence'])

    def test_frequency_and_nonpercent_unit_mismatch_still_rejected(self):
        _, host = self.prepare()
        bindings = self.fixture.bindings(host)
        parameters = host['calculations'][0]['parameters']
        validate_calculation_context(METHOD, bindings, parameters)
        for unit in ('比例', '%/月', '百分点', '倍'):
            changed = copy.deepcopy(bindings)
            changed['management_a']['unit'] = unit
            with self.assertRaises(ContractError):
                validate_calculation_context(METHOD, changed, parameters)
        changed = copy.deepcopy(bindings)
        changed['management_a']['calculation_context']['rate_period'] = 'monthly'
        with self.assertRaises(ContractError):
            validate_calculation_context(METHOD, changed, parameters)

    def test_annual_frequency_cannot_be_borrowed_from_another_original(self):
        task, host = self.prepare()
        host['evidence_specs'][0]['calculation_context_basis']['rate_period'] = copy.deepcopy(
            host['evidence_specs'][1]['calculation_context_basis']['rate_period'])
        final = self.agent.start(task['id'], host)
        self.assertEqual(final['status'], '部分完成')
        self.assertFalse(final['calculations'])
        self.assertTrue(final['rejected_evidence'])

    def test_variants_do_not_expand_other_registered_methods(self):
        self.assertTrue(valid_calculation_input_unit(METHOD, 'management_a', '%'))
        self.assertFalse(valid_calculation_input_unit('fee_difference_v1', 'fee_a', '%'))
        self.assertFalse(valid_calculation_input_unit(METHOD, 'sales_service', '%'))
        self.assertFalse(valid_calculation_input_unit('unregistered', 'management_a', '%'))

    def test_supplement_candidate_requires_four_distinct_rate_records(self):
        self.assertTrue(calculation_input_units_available(METHOD, ['%'] * 4))
        self.assertTrue(calculation_input_units_available(METHOD, ['%', '%/年', '%', '%/年']))
        self.assertFalse(calculation_input_units_available(METHOD, ['%'] * 3))
        self.assertFalse(calculation_input_units_available(METHOD, ['%', '%', '%', '比例']))
        self.assertTrue(calculation_input_units_available('gross_margin_v1', ['百万元'] * 2))
        self.assertFalse(calculation_input_units_available('gross_margin_v1', ['百万元']))
        task, host = self.prepare()
        final = self.agent.start(task['id'], host)
        candidate = copy.deepcopy(final)
        candidate['questions'][0]['status'] = '未回答'
        candidate['calculation_specs'] = []
        self.assertIn(METHOD, self.agent._supplement_options(candidate)['methods'])

    def test_document_scope_and_false_unit_rewrite_are_still_rejected(self):
        _, host = self.prepare()
        bindings = self.fixture.bindings(host)
        bindings['management_a']['usage_scope'] = 'document_content'
        with self.assertRaises(ContractError):
            validate_calculation_context(METHOD, bindings, host['calculations'][0]['parameters'])
        task, host = self.prepare()
        host['evidence_specs'][0]['unit'] = '%/年'
        final = self.agent.start(task['id'], host)
        self.assertEqual(final['status'], '部分完成')
        self.assertFalse(final['calculations'])
        self.assertTrue(final['rejected_evidence'])

    def test_knowledge_revision_preserves_original_entry_and_arithmetic_version(self):
        identifier = METHOD_REGISTRY[METHOD]['knowledge_id']
        old = RETAINED_VERSIONS[(identifier, 1)]
        self.assertEqual(old['version'], 1)
        self.assertNotIn('previous_version', old)
        latest = ResearchKnowledge().read('ETF', '比较费率', [METHOD])
        self.assertEqual(latest['index'][identifier], 2)
        historical = copy.deepcopy(latest)
        historical['entries'] = [copy.deepcopy(old) if entry['id'] == identifier else entry for entry in historical['entries']]
        historical['index'][identifier] = 1
        ResearchKnowledge().validate_package(historical)
        self.assertEqual(fingerprint(old), fingerprint(RETAINED_VERSIONS[(identifier, 1)]))
        self.assertEqual(METHOD_REGISTRY[METHOD]['implementation_version'], 1)
        self.assertIn('raw_unit与unit仍填%', PHASE_RULES['extract'])

    def test_versioned_catalog_keeps_deepcopy_dependency_and_read_only_assertions(self):
        knowledge = ResearchKnowledge()
        before = fingerprint(ENTRIES)
        package = knowledge.read('个股', '现金流')
        changed = copy.deepcopy(package)
        next(entry for entry in changed['entries'] if entry['id'] == 'rh_k_cash_profit')['verification_indicators'].append('伪造指标')
        with self.assertRaises(ContractError):
            knowledge.validate_package(changed)
        changed = copy.deepcopy(package)
        changed['index']['rh_k_cash_profit'] = 99
        with self.assertRaises(ContractError):
            knowledge.validate_package(changed)
        changed = copy.deepcopy(package)
        changed['entries'] = [entry for entry in changed['entries'] if entry['id'] != 'rh_k_fact']
        changed['index'].pop('rh_k_fact')
        with self.assertRaises(ContractError):
            knowledge.validate_package(changed)
        with self.assertRaises(ContractError):
            knowledge.publish(package)
        self.assertEqual(fingerprint(ENTRIES), before)
        for entry in ENTRIES[:8]:
            version = 2 if entry['id'] == METHOD_REGISTRY[METHOD]['knowledge_id'] else 1
            self.assertEqual(entry['version'], version)
            self.assertEqual(entry, RETAINED_VERSIONS[(entry['id'], version)])


if __name__ == '__main__':
    unittest.main()
