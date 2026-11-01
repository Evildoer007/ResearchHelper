"""Typed research selections serialize to the same existing vendor contract."""
import copy
import unittest

from tests import ROOT
from tests import test_industry_financial_batch as fixtures
from researchhelper.contracts import ContractError
from researchhelper.financial_sources import (financial_query_shapes, semantic_query_parameters,
    validate_financial_query)
from researchhelper.output_schema import (_planned_request_schemas, _matches, object_schema,
    array_schema, decode_acquisition_output, decode_planning_output, _identity_profile_binding)
from researchhelper.planning import input_fingerprint
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.reports import compose
import tempfile

QUERY = fixtures.QUERY


class SemanticFinancialRequestsTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.IndustryFinancialBatchTests(); fixture.setUp()
        self.task = dict(fixture.task, semantic_query_encoding=1)
        self.tool = financial_query_shapes(self.task)[0]
        self.contract = dict(tools=[self.tool], callable_codes=[], question_ids=['q1'])
        self.schema = {'anyOf':_planned_request_schemas(self.task, self.contract)}
        self.parameters = dict(query_kind='financial_batch', topic='证券', discovery_kind='行业',
            year=2026, report_period='半年报', fields=['营业收入', '归属于母公司所有者的净利润'])

    def request(self, parameters=None):
        return dict(id='sector_values', source='ifind', operation='smart_stock_picking',
            parameters=parameters or self.parameters, question_ids=['q1'], selection_reason='同期间取数')

    def test_new_schema_accepts_typed_fields_instead_of_vendor_string_copying(self):
        self.assertTrue(_matches(self.request(), self.schema))
        self.assertFalse(_matches(self.request(dict(searchstring=QUERY, searchtype='stock')), self.schema))
        self.assertEqual(semantic_query_parameters(self.parameters), dict(searchstring=QUERY, searchtype='stock'))

    def test_discovery_selection_preserves_topic_without_inferring_an_identity(self):
        selected = dict(query_kind='object_discovery', topic='证券', discovery_kind='行业')
        self.assertTrue(_matches(self.request(selected), self.schema))
        self.assertEqual(semantic_query_parameters(selected), dict(searchstring='证券行业股票', searchtype='stock'))

    def test_acquisition_decoder_serializes_but_never_changes_the_submitted_selection(self):
        wire = dict(version=1, binding_ref='fixture', data_requests=[self.request()], stop_reason='')
        schema = object_schema(dict(version={'type':'integer','enum':[1]},
            binding_ref={'type':'string','enum':['fixture']},
            data_requests=array_schema(self.schema), stop_reason={'type':'string'}))
        schema['description'] = _identity_profile_binding(False)
        original = copy.deepcopy(wire)
        decoded = decode_acquisition_output(wire, schema, self.contract)
        self.assertEqual(wire, original)
        self.assertEqual(decoded['data_requests'][0]['parameters'], dict(searchstring=QUERY, searchtype='stock'))
        self.assertEqual(validate_financial_query(self.task, decoded['data_requests'][0]['parameters'])['query_kind'], 'financial_batch')

    def test_first_planning_decoder_uses_the_same_serialization(self):
        wire = dict(version=2, questions=[dict(id='q1', evidence_needs=[])],
            data_requests=[self.request()], calculations=[])
        schema = object_schema(dict(version={'type':'integer','enum':[2]},
            questions=array_schema(object_schema(dict(id={'type':'string'}, evidence_needs=array_schema({'type':'string'})))),
            data_requests=array_schema(self.schema), calculations=array_schema(object_schema({}))))
        schema['description'] = _identity_profile_binding(False)
        decoded = decode_planning_output(wire, schema)
        self.assertEqual(decoded['data_requests'][0]['parameters'], dict(searchstring=QUERY, searchtype='stock'))
        self.assertEqual(wire['data_requests'][0]['parameters'], self.parameters)

    def test_wrong_fields_units_duplicate_fields_and_extra_conditions_are_not_repaired(self):
        for selection in (dict(self.parameters, fields=['归母净利润']),
                          dict(self.parameters, fields=['营业收入', '营业收入']),
                          dict(self.parameters, year=True), dict(self.parameters, amount_unit='元'),
                          dict(self.parameters, limit=10)):
            with self.subTest(selection=selection):
                self.assertFalse(_matches(self.request(selection), self.schema))
                with self.assertRaises(ContractError): semantic_query_parameters(selection)

    def test_unrelated_theme_or_future_period_still_fails_task_validation(self):
        for selection in (dict(self.parameters, topic='煤炭'), dict(self.parameters, year=2027)):
            with self.subTest(selection=selection), self.assertRaises(ContractError):
                validate_financial_query(self.task, semantic_query_parameters(selection))

    def test_legacy_task_keeps_original_wire_and_rejects_new_encoding(self):
        old = dict(self.task); old.pop('semantic_query_encoding')
        schema = dict(anyOf=_planned_request_schemas(old, self.contract))
        self.assertTrue(_matches(self.request(dict(searchstring=QUERY, searchtype='stock')), schema))
        self.assertFalse(_matches(self.request(), schema))

    def test_unknown_versions_do_not_enable_the_new_wire(self):
        for version in (True, '1', 2):
            with self.subTest(version=version), self.assertRaises(ContractError):
                financial_query_shapes(dict(self.task, semantic_query_encoding=version))

    def test_new_default_policy_is_fingerprinted_and_frozen(self):
        with tempfile.TemporaryDirectory(prefix='rh-semantic-selection-') as directory:
            agent = ResearchAgent(ResearchArchive(directory), ROOT, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task = agent.create(self.task['question'], research_mode='current')
            self.assertEqual(task['semantic_query_encoding'], 1)
            agent._adopt_method_knowledge(task)
            task['gaps'] = ['离线合成检查，未取数']
            self.assertEqual(compose(task)['semantic_query_encoding'], 1)
            old = copy.deepcopy(task); old.pop('semantic_query_encoding')
            self.assertNotEqual(input_fingerprint(task), input_fingerprint(old))
