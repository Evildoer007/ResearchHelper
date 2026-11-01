"""Fixed request-parameter compilation, not retrying the paid R138 study."""
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.evidence import data_request_contract
from researchhelper.planning import input_fingerprint
from researchhelper.market_series import SERIES_FAMILIES, series_settings, validate_series_request
from researchhelper.output_schema import (planning_output_schema, decode_planning_output, provider_output_schema,
    acquisition_output_schema, decode_acquisition_output, SERIES_PARAMETER_BINDING)

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT/'result/acceptance/one-sentence-research-d9rimpn2'
POLICY = 'series_parameter_selection_version'


def prepare_fixture():
    return dict(task=json.loads((STUDY/'task.json').read_text()),
        explicit_task=json.load(gzip.open(ROOT/'result/acceptance/etf-nav-explicit-hu0pauzk/task.json.gz', 'rt', encoding='utf-8')),
        hashes={name: hashlib.sha256((STUDY/name).read_bytes()).hexdigest()
                for name in ('receipt.json', 'task.json')})


def literal(schema):
    if 'anyOf' in schema: return literal(schema['anyOf'][0])
    if 'enum' in schema: return schema['enum'][0]
    kind = schema['type']
    if kind == 'object': return {key: literal(value) for key, value in schema['properties'].items()}
    if kind == 'array': return []
    if kind == 'string': return 'series_control'
    if kind == 'null': return None
    if kind == 'boolean': return True
    raise AssertionError('Unsupported control type')


class SeriesParameterSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get('RESEARCHHELPER_SERIES_PARAMETER_FIXTURE')
        cls.fixture = json.loads(gzip.decompress(Path(supplied).read_bytes())) if supplied else prepare_fixture()

    def setUp(self):
        guard = patch('socket.socket.connect', side_effect=AssertionError('No model or data requests'))
        guard.start(); self.addCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); self.addCleanup(worker.stop)
        folder = tempfile.TemporaryDirectory(prefix='rh-series-parameters-'); self.addCleanup(folder.cleanup)
        resources = Path(os.environ.get('RESEARCHHELPER_SERIES_PARAMETER_RESOURCES', str(ROOT)))
        self.agent = ResearchAgent(ResearchArchive(folder.name), resources, offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create('核对510300.SH、600519.SH与000300.SH的登记日期序列',
            entry='混合', objects=['510300.SH', '600519.SH', '000300.SH'], research_mode='current')

    def request(self, code='510300.SH', field='ths_unit_nvnf_fund'):
        return dict(id='series_control', source='ifind', operation='date_sequence', question_ids=['q1'],
            selection_reason='只选择登记字段，不提交固定参数', parameters=dict(codes=code,
                startdate='2026-10-12', enddate='2026-10-16', functionpara=series_settings(self.task, code),
                indipara=[dict(indicator=field)]))

    def wire(self, request=None, task=None):
        task = self.task if task is None else task
        schema = planning_output_schema(task); wire = literal(schema)
        wire['data_requests'] = [self.request() if request is None else copy.deepcopy(request)]
        return wire, schema

    def test_actual_old_failure_is_preserved_and_still_rejected(self):
        old = copy.deepcopy(self.fixture['task'])
        self.assertNotIn(POLICY, old)
        request = next(item for item in old['reasoning_state']['rejected_plan']['proposed']['data_requests']
                       if item['operation'] == 'date_sequence')
        self.assertEqual(request['parameters']['indipara'][0]['indiparams'], [])
        with self.assertRaises(ContractError): validate_series_request(old, request['operation'], request['parameters'])

    def test_selected_etf_field_compiles_the_registered_empty_string_placeholder(self):
        wire, schema = self.wire(); before = canonical(wire)
        result = decode_planning_output(wire, schema)
        self.assertEqual(result['data_requests'][0]['parameters']['indipara'],
                         [dict(indicator='ths_unit_nvnf_fund', indiparams=[''])])
        validate_series_request(self.task, 'date_sequence', result['data_requests'][0]['parameters'])
        self.assertEqual(canonical(wire), before)

    def test_every_family_keeps_its_exact_ordered_parameters(self):
        for family, code in (('ETF', '510300.SH'), ('个股', '600519.SH'), ('指数', '000300.SH')):
            for field, expected in SERIES_FAMILIES[family].items():
                wire, schema = self.wire(self.request(code, field))
                request = decode_planning_output(wire, schema)['data_requests'][0]
                self.assertEqual(request['parameters']['indipara'][0]['indiparams'], expected)
                validate_series_request(self.task, 'date_sequence', request['parameters'])

    def test_extra_parameters_are_rejected_not_deleted_or_repaired(self):
        for value in ([], [''], ['100'], ['', '100']):
            request = self.request(); request['parameters']['indipara'][0]['indiparams'] = value
            wire, schema = self.wire(request)
            with self.assertRaises(ContractError): decode_planning_output(wire, schema)

    def test_unknown_or_cross_family_fields_are_not_inferred(self):
        for field in ('invented_indicator', 'ths_pe_ttm_stock'):
            wire, schema = self.wire(self.request(field=field))
            with self.assertRaises(ContractError): decode_planning_output(wire, schema)

    def test_empty_or_duplicate_field_selection_is_rejected(self):
        for items in ([], [dict(indicator='ths_unit_nvnf_fund')]*2):
            request = self.request(); request['parameters']['indipara'] = items
            wire, schema = self.wire(request)
            with self.assertRaises(ContractError): decode_planning_output(wire, schema)

    def test_local_bindings_do_not_leak_as_unknown_provider_keywords(self):
        _, schema = self.wire()
        self.assertIn(SERIES_PARAMETER_BINDING, canonical(schema))
        self.assertNotIn(SERIES_PARAMETER_BINDING, canonical(provider_output_schema(schema)))

    def test_acquisition_and_planning_use_the_same_parameter_binding(self):
        description = dict(data_request_contract=data_request_contract(self.task))
        schema = acquisition_output_schema(self.task, description)
        wire = literal(schema); wire.update(data_requests=[self.request()], stop_reason='')
        result = decode_acquisition_output(wire, schema, {})
        validate_series_request(self.task, 'date_sequence', result['data_requests'][0]['parameters'])
        self.assertEqual(result['data_requests'][0]['parameters']['indipara'][0]['indiparams'], [''])

    def test_old_tasks_keep_the_explicit_full_parameter_contract(self):
        old = copy.deepcopy(self.task); old.pop(POLICY)
        request = self.request(); request['parameters']['indipara'][0]['indiparams'] = ['']
        wire, schema = self.wire(request, old)
        self.assertNotIn(SERIES_PARAMETER_BINDING, canonical(schema))
        self.assertEqual(decode_planning_output(wire, schema)['data_requests'][0], request)

    def test_binding_uses_the_locked_schema_not_a_later_mutable_registry(self):
        wire, schema = self.wire()
        with patch.dict(SERIES_FAMILIES['ETF'], ths_unit_nvnf_fund=['changed']):
            result = decode_planning_output(wire, schema)
        self.assertEqual(result['data_requests'][0]['parameters']['indipara'][0]['indiparams'], [''])

    def test_unknown_policy_versions_are_not_silently_enabled(self):
        for value in (True, 0, 2, '1'):
            task = copy.deepcopy(self.task); task[POLICY] = value
            with self.assertRaises(ContractError): planning_output_schema(task)
        for registry in (None, [], dict(version=2.0, fields={}), dict(version=3, fields={}), dict(version=2, fields=[])):
            task = copy.deepcopy(self.task); task['series_parameter_registry'] = registry
            with self.assertRaises(ContractError): planning_output_schema(task)

    def test_policy_is_bound_to_input_and_archive_without_budget_reset(self):
        self.assertEqual(self.task[POLICY], 1)
        old = copy.deepcopy(self.task); old.pop(POLICY)
        self.assertNotEqual(input_fingerprint(old), input_fingerprint(self.task))
        stored = self.agent.archive.get(self.task['id'])
        self.assertEqual(stored[POLICY], 1)
        self.assertEqual(stored['ledger'], self.task['ledger'])
        self.assertEqual(stored['ledger']['used']['model_calls'], 0)
        changed = copy.deepcopy(self.task); changed['series_parameter_registry']['fields']['ths_unit_nvnf_fund'] = []
        self.assertNotEqual(input_fingerprint(changed), input_fingerprint(self.task))

    def test_closed_real_planning_schema_hash_is_not_changed(self):
        old = copy.deepcopy(self.fixture['task'])
        event = next(item for item in old['ledger']['dispatches'] if item['kind'] == 'model_calls')
        self.assertEqual(fingerprint(planning_output_schema(old)), event['output_schema_hash'])

    def test_report_freezes_the_policy_without_approving_facts(self):
        from researchhelper.reports import compose
        self.agent._adopt_method_knowledge(self.task)
        self.task['status'] = '部分完成'
        self.agent._gap(self.task, '固定参数选择控制，不是实际取数', category='question_coverage')
        report = compose(self.task)
        self.assertEqual(report[POLICY], 1)
        self.assertEqual(report['series_parameter_registry'], self.task['series_parameter_registry'])
        report['series_parameter_registry']['fields'].clear()
        self.assertTrue(self.task['series_parameter_registry']['fields'])
        self.assertFalse(report['evidence']); self.assertFalse(report['calculations'])

    def test_compiled_request_matches_the_retained_successful_provider_request(self):
        actual = next(item for item in self.fixture['explicit_task']['source_records'] if item['operation'] == 'date_sequence')
        wire, schema = self.wire()
        self.assertEqual(decode_planning_output(wire, schema)['data_requests'][0]['parameters'], actual['parameters'])
        self.assertEqual(actual['snapshot']['errorcode'], 0)

    def test_short_parameter_prompt_requires_the_actual_structured_codec(self):
        from researchhelper.reasoning import series_parameter_selection_rule
        for phase in ('plan', 'acquire'):
            self.assertEqual(series_parameter_selection_rule(self.task, phase, None), '')
            self.assertIn('不提交indiparams', series_parameter_selection_rule(self.task, phase, {}))
        old = copy.deepcopy(self.task); old.pop(POLICY)
        self.assertEqual(series_parameter_selection_rule(old, 'plan', {}), '')
        self.assertEqual(series_parameter_selection_rule(self.task, 'analyze', {}), '')
