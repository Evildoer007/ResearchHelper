"""Read-only replay of real retained financial tables and compact selectors.

No task is reopened, no provider/model is called and no report is accepted as
new autonomous App research. The fake model only exercises the shared decoder.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.archive import ResearchArchive
import researchhelper.engine as engine_module
from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import candidate_evidence, validate_calculation_context_basis
from researchhelper.financial_sources import (FINANCIAL_VALUE_FIELDS,
    financial_table_candidates, expand_financial_value_references)
from researchhelper.planning import question_specs
from researchhelper.reasoning import (evidence_contract, expand_material_context_references,
    model_object, validate_model_specs)

ROOT = Path(__file__).resolve().parents[1]
RETAINED = ROOT / 'result/acceptance/skill-host-financial-20261015-v2'


class FinancialValueReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = [RETAINED / name for name in ('task.json', 'reading.json', 'host-proposal.json',
            'result/researchhelper/rh_1cd38423decc4c6b9b46ea53ec82f05b/research-v1.html')]
        cls.hashes = {str(file): hashlib.sha256(file.read_bytes()).hexdigest() for file in cls.files}
        cls.original_task = json.loads(cls.files[0].read_text())
        cls.reading = json.loads(cls.files[1].read_text())
        cls.original_specs = json.loads(cls.files[2].read_text())['evidence_specs']
        guard = patch('socket.socket.connect', side_effect=AssertionError('Network forbidden'))
        guard.start()
        cls.addClassCleanup(guard.stop)

    @classmethod
    def tearDownClass(cls):
        assert all(hashlib.sha256(Path(name).read_bytes()).hexdigest() == value for name, value in cls.hashes.items())

    def setUp(self):
        self.task = copy.deepcopy(self.original_task)
        self.views = copy.deepcopy(self.reading['views'])
        self.record = next(record for record in self.task['source_records'] if record['kind'] == 'filing')
        self.view = next(view for view in self.views if view['id'] == self.record['id'])
        self.candidates = self.candidates_for()['cells']

    def candidates_for(self, task=None, record=None, view=None, maximum=16):
        return financial_table_candidates(task or self.task, record or self.record,
                                          self.view if view is None else view, maximum)

    def compact(self):
        result = copy.deepcopy(self.original_specs)
        for spec in result:
            choice = next(cell for cell in self.candidates
                if cell['metric'] == spec['calculation_context']['metric']
                and cell['proposal_fields']['period'] == spec['period'])
            for field in FINANCIAL_VALUE_FIELDS:
                spec.pop(field)
            spec['record_locator'] = {key: value for key, value in spec['record_locator'].items()
                                     if key in ('period_part', 'period_quote')}
            spec['financial_value_reference'] = choice['candidate_id']
        return result

    def expand(self, specs=None, task=None, views=None):
        return expand_financial_value_references(self.task if task is None else task,
            self.compact() if specs is None else specs, self.views if views is None else views)

    def context_compact(self, specs):
        sources = {source['artifact_id']: source for source in evidence_contract(self.task, self.views)['sources']}
        specs = copy.deepcopy(specs)
        for spec in specs:
            bases = spec.pop('calculation_context_basis')
            spec['material_context_reference'] = dict(
                candidate_id=sources[spec['artifact_id']]['material_context_reference']['candidate_id'],
                bases={key: {name: anchor[name] for name in ('part', 'quote')} for key, anchor in bases.items()})
        return specs

    def payload(self):
        return dict(question=self.task['question'], intent=self.task['intent'],
            questions=question_specs(self.task), untrusted_sources=self.views,
            evidence_contract=evidence_contract(self.task, self.views))

    def altered_parsed_row(self, token):
        """Synthetic locator semantics only; unchanged PDF bytes cannot pass provenance."""
        task = copy.deepcopy(self.task)
        record = next(record for record in task['source_records'] if record['id'] == self.record['id'])
        view = copy.deepcopy(self.view)
        old = next(cell for cell in self.candidates if cell['metric'] == 'net_profit')['proposal_fields']
        locator = old['record_locator']['part']
        cells = old['text'].split('\t'); cells[2] = token
        row = '\t'.join(cells)
        for collection in (record['snapshot']['material']['parts'], view['parts']):
            part = next(part for part in collection if part['locator'] == locator)
            part['text'] = part['text'].replace(old['text'], row)
        record['hash'] = fingerprint(record['snapshot']); view['snapshot_hash'] = record['hash']
        return task, record, view

    def test_actual_current_and_comparison_periods_are_kept_separate(self):
        self.assertEqual(len(self.candidates), 8)
        self.assertEqual({cell['proposal_fields']['period'] for cell in self.candidates},
                         {'2026年半年度', '2025年半年度'})
        self.assertEqual({cell['metric'] for cell in self.candidates},
                         {'revenue', 'operating_cost', 'net_profit', 'operating_cash_flow'})

    def test_current_three_values_are_the_exact_previously_reviewed_report_values(self):
        for spec in self.original_specs:
            cell = next(cell for cell in self.candidates if cell['metric'] == spec['calculation_context']['metric']
                        and cell['proposal_fields']['period'] == spec['period'])
            for key in FINANCIAL_VALUE_FIELDS:
                self.assertEqual(cell['proposal_fields'][key], spec[key], key)

    def test_continuation_rows_keep_their_own_header_and_note_columns(self):
        cell = next(cell for cell in self.candidates if cell['metric'] == 'net_profit')
        locator = cell['proposal_fields']['record_locator']
        self.assertEqual(locator['part'], '第31页表1')
        self.assertEqual(locator['financial_column']['header_part'], '第30页表2')
        self.assertEqual(locator['financial_column']['column_index'], 2)
        self.assertEqual(len(locator['financial_column']['row_quote'].split('\t')), 4)

    def test_candidates_are_readonly_and_never_source_or_financial_approval(self):
        before = fingerprint((self.task, self.record, self.views))
        result = self.candidates_for()
        self.assertFalse(result['candidate_is_approval'])
        self.assertFalse(result['absence_is_proof'])
        self.assertTrue(result['scan_complete'])
        self.assertLessEqual(result['scan_units'], result['maximum_scan_units'])
        self.assertEqual(fingerprint((self.task, self.record, self.views)), before)
        self.assertFalse(any('source_check' in cell or 'applicability_check' in cell for cell in result['cells']))

    def test_bound_is_explicit_and_invalid_bounds_are_rejected(self):
        result = self.candidates_for(maximum=1)
        self.assertEqual(len(result['cells']), 1)
        self.assertFalse(result['scan_complete'])
        for value in (0, True, 65, '16'):
            with self.subTest(value=value), self.assertRaises(ContractError): self.candidates_for(maximum=value)

    def test_unread_amount_header_or_unit_page_cannot_be_borrowed(self):
        for locator in ('第31页表1', '第30页表2', '第30页'):
            view = copy.deepcopy(self.view)
            view['parts'] = [part for part in view['parts'] if part['locator'] != locator]
            cells = self.candidates_for(view=view)['cells']
            self.assertFalse(any(cell['metric'] == 'net_profit' for cell in cells), locator)

    def test_wrong_snapshot_missing_text_duplicate_parts_or_foreign_record_are_rejected(self):
        variants = []
        view = copy.deepcopy(self.view); view['snapshot_hash'] = 'changed'; variants.append(view)
        view = copy.deepcopy(self.view); view['parts'][0]['text'] = 'forged'; variants.append(view)
        view = copy.deepcopy(self.view); view['parts'].append(view['parts'][0]); variants.append(view)
        for view in variants:
            with self.subTest(view=view['id']), self.assertRaises(ContractError): self.candidates_for(view=view)
        record = copy.deepcopy(self.record); record['id'] = 'foreign'
        with self.assertRaises(ContractError): self.candidates_for(record=record)

    def test_changed_retained_bytes_do_not_produce_locations(self):
        task = copy.deepcopy(self.task)
        record = next(record for record in task['source_records'] if record['id'] == self.record['id'])
        record['snapshot']['material']['parts'][0]['text'] += 'changed'
        with self.assertRaises(ContractError): self.candidates_for(task=task, record=record)

    def test_without_financial_method_or_registered_table_parser_there_is_no_shortcut(self):
        task = copy.deepcopy(self.task)
        task['calculation_specs'] = []
        for question in task['questions']: question['method_needs'] = []
        self.assertFalse(self.candidates_for(task=task)['cells'])
        task = copy.deepcopy(self.task)
        record = next(record for record in task['source_records'] if record['id'] == self.record['id'])
        record['snapshot']['material']['extract_version'] = '1'
        record['hash'] = fingerprint(record['snapshot'])
        view = dict(self.view, snapshot_hash=record['hash'])
        self.assertFalse(self.candidates_for(task=task, record=record, view=view)['cells'])

    def test_expansion_is_lossless_and_preserves_caller_objects(self):
        specs = self.compact(); before = fingerprint((self.task, specs, self.views))
        self.assertEqual(self.expand(specs), self.original_specs)
        self.assertEqual(fingerprint((self.task, specs, self.views)), before)

    def test_nonnumeric_context_and_observation_basis_stay_explicit(self):
        specs = self.compact(); specs[0]['calculation_context']['currency'] = 'USD'
        expanded = self.expand(specs)
        self.assertEqual(expanded[0]['calculation_context']['currency'], 'USD')
        item = dict(expanded[0], task_id=self.task['id'], research_version=self.task['version'])
        with self.assertRaises(ContractError):
            validate_calculation_context_basis(item, self.task['source_records'])
        self.assertEqual(expanded[0]['record_locator']['period_quote'], '2026 年 6 月 30 日')
        self.assertEqual(expanded[0]['observation_date'], '2026-06-30')

    def test_parent_context_cannot_borrow_the_consolidated_table(self):
        specs = self.compact(); specs[0]['calculation_context']['accounting_scope'] = 'parent'
        item = dict(self.expand(specs)[0], task_id=self.task['id'], research_version=self.task['version'])
        with self.assertRaises(ContractError):
            validate_calculation_context_basis(item, self.task['source_records'])

    def test_full_canonical_format_remains_unchanged(self):
        self.assertIs(expand_financial_value_references(self.task, self.original_specs), self.original_specs)

    def test_numeric_override_is_rejected_including_null(self):
        for field in FINANCIAL_VALUE_FIELDS:
            specs = self.compact(); specs[0][field] = None
            with self.subTest(field=field), self.assertRaises(ContractError): self.expand(specs)

    def test_wrong_metric_period_or_full_locator_is_rejected(self):
        for field, value in (('metric', 'operating_cost'), ('period', '2025年半年度')):
            specs = self.compact(); specs[0]['calculation_context'][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError): self.expand(specs)
        for locator in ({'part': '第30页表2'}, {'financial_column': {}}, {'period_part': '第28页'}):
            specs = self.compact(); specs[0]['record_locator'] = locator
            with self.subTest(locator=locator), self.assertRaises(ContractError): self.expand(specs)

    def test_unknown_wrong_source_duplicate_or_malformed_selector_is_rejected(self):
        for value in ('', None, {}, 'rh_fin_fake'):
            specs = self.compact(); specs[0]['financial_value_reference'] = value
            with self.subTest(value=value), self.assertRaises(ContractError): self.expand(specs)
        specs = self.compact(); specs[0]['artifact_id'] = 'foreign'
        with self.assertRaises(ContractError): self.expand(specs)
        specs = self.compact(); specs.append(copy.deepcopy(specs[0]))
        with self.assertRaises(ContractError): self.expand(specs)

    def test_old_selection_cannot_cross_task_version_batch_cutoff_or_input(self):
        for key, value in (('id', 'foreign'), ('version', 2), ('batch', 3),
                           ('cutoff', '2026-10-14T18:00:00+08:00'), ('input_hash', 'changed')):
            task = copy.deepcopy(self.task); task[key] = value
            with self.subTest(key=key), self.assertRaises(ContractError): self.expand(task=task)

    def test_old_selection_cannot_expand_without_the_same_exposed_reading(self):
        with self.assertRaises(ContractError): self.expand(views=[])
        with self.assertRaises(ContractError): expand_financial_value_references(self.task, self.compact())
        view = copy.deepcopy(self.view); view['parts'].reverse()
        with self.assertRaises(ContractError): self.expand(views=[view])

    def test_both_compact_codecs_expand_to_the_original_unmodified_specs(self):
        specs = self.context_compact(self.compact())
        numeric = self.expand(specs)
        self.assertEqual(expand_material_context_references(self.task, numeric, self.views), self.original_specs)

    def test_ordinary_model_and_host_decode_the_same_cells(self):
        class Model:
            def reason_phase(self, task, messages, phase):
                self.messages = messages
                return json.dumps(dict(evidence_specs=compact), ensure_ascii=False)
        for compact in (self.compact(), self.context_compact(self.compact())):
            with self.subTest(compact_context='material_context_reference' in compact[0]):
                model = Model()
                result, receipt = model_object(model, self.task, 'extract', self.payload(), {'evidence_specs'})
                self.assertEqual(result['evidence_specs'], self.original_specs)
                self.assertEqual(receipt['output_hash'], fingerprint(result))
                self.assertEqual(receipt['financial_value_reference_version'], 1)
                self.assertEqual(receipt['financial_value_wire_hash'], fingerprint(dict(evidence_specs=compact)))
                task = copy.deepcopy(self.task); task['source_readings'] = [copy.deepcopy(self.reading)]
                host = ResearchAgent._validate_host_input(task, dict(
                    reading_hash=self.reading['reading_hash'], evidence_specs=compact))
                self.assertEqual(host['evidence_specs'], self.original_specs)
                self.assertIn('financial_value_reference', canonical(model.messages))

    def test_invalid_model_selector_is_not_repaired(self):
        compact = self.compact(); compact[0]['financial_value_reference'] = 'rh_fin_fake'
        class Model:
            def reason_phase(self, task, messages, phase):
                return json.dumps(dict(evidence_specs=compact), ensure_ascii=False)
        with self.assertRaises(ContractError):
            model_object(Model(), self.task, 'extract', self.payload(), {'evidence_specs'})

    def test_original_source_and_independent_applicability_gates_still_apply(self):
        expanded = self.expand()
        validate_model_specs(self.task, expanded)
        for spec in expanded:
            evidence = candidate_evidence(self.task, self.record, spec)
            self.assertEqual(evidence['source_check'], '通过')
            self.assertEqual(evidence['applicability_check'], '待核验')
            assessed = ResearchAnalyzer().assess(self.task, evidence, spec['review'], self.task['source_records'])
            self.assertEqual(assessed['applicability_check'], '通过')
        self.assertFalse(self.task['evidence'])

    def test_serialized_submission_shrinks_without_losing_fields(self):
        before = self.context_compact(self.original_specs)
        after = self.context_compact(self.compact())
        self.assertLess(len(canonical(after)), len(canonical(before)))
        restored = expand_material_context_references(self.task, self.expand(after), self.views)
        self.assertEqual(restored, self.original_specs)

    def test_negative_token_is_preserved_without_absolute_value_or_zero_fill(self):
        task, record, view = self.altered_parsed_row('−8.00')
        cell = next(cell for cell in self.candidates_for(task, record, view)['cells']
                    if cell['metric'] == 'net_profit' and cell['proposal_fields']['period'] == '2026年半年度')
        self.assertEqual(cell['proposal_fields']['raw_token'], '−8.00')
        self.assertEqual(cell['proposal_fields']['value'], -8)

    def test_missing_percentage_or_unrepresentable_decimal_is_not_a_literal_amount(self):
        for token in ('', 'NaN', '12%', '1.123456789012345678'):
            task, record, view = self.altered_parsed_row(token)
            cells = self.candidates_for(task, record, view)['cells']
            self.assertFalse(any(cell['metric'] == 'net_profit' and
                cell['proposal_fields']['period'] == '2026年半年度' for cell in cells), token)

    def test_modified_parse_cannot_approve_a_number_against_unchanged_original_bytes(self):
        task, record, view = self.altered_parsed_row('-8.00')
        choice = next(cell for cell in self.candidates_for(task, record, view)['cells']
                      if cell['metric'] == 'net_profit' and cell['proposal_fields']['period'] == '2026年半年度')
        spec = copy.deepcopy(self.original_specs[1])
        spec.update(choice['proposal_fields'])
        with self.assertRaises(ContractError): candidate_evidence(task, record, spec)

    def test_isolated_host_replay_reaches_calculations_and_saved_report_without_new_calls(self):
        # Copy an unfinished archived fixture into a disposable, separate Store.
        # This is deterministic controller replay, not reopening the original
        # task or proving autonomous App-model financial research succeeded.
        origin = getattr(engine_module, '__frozen_origin__', None)
        resources = Path(origin.split('!/PYZ.pyz/')[0]).parents[2] if origin else None
        with tempfile.TemporaryDirectory(prefix='financial-cell-report-', dir=ROOT / 'result/acceptance') as temporary:
            agent = ResearchAgent(ResearchArchive(Path(temporary) / 'store'), resources, offline=True)
            try:
                task = copy.deepcopy(self.task)
                task['revision'] = 0
                task['source_readings'] = [copy.deepcopy(self.reading)]
                used = copy.deepcopy(task['ledger']['used'])
                agent.archive.save(task)
                host = json.loads((RETAINED / 'host-proposal.json').read_text())
                host['evidence_specs'] = self.context_compact(self.compact())
                result = agent.start(task['id'], host)
                self.assertEqual(result['status'], '已完成', result.get('gaps'))
                self.assertEqual(len(result['evidence']), 3)
                self.assertEqual(len(result['calculations']), 2)
                self.assertEqual(result['ledger']['used']['data_requests'], used['data_requests'])
                self.assertEqual(result['ledger']['used']['model_calls'], used['model_calls'])
                self.assertTrue(result['delivery']['html_sha256'])
                self.assertFalse(result['gaps'])
                self.assertAlmostEqual(result['calculations'][0]['output']['value'], 50.751571748679424)
                self.assertAlmostEqual(result['calculations'][1]['output']['value'], 153.56427451302872)
                report = agent.archive.report(task['id'], result['version'])
                self.assertTrue(report['html_integrity_verified'])
                self.assertEqual(report['digest'], result['delivery']['digest'])
                self.assertIn('echarts.init', report['html'])
            finally:
                agent.close_runtime()


if __name__ == '__main__': unittest.main()
