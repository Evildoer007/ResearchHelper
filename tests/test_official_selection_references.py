"""Independent reference-selection tests; all publications are synthetic."""
import copy
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.acquisition import initial_acquisition_coverage
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector, data_request_contract, validate_source_request
from researchhelper.official_sources import selection_references, validate_request, validate_original
from researchhelper.output_schema import _planned_request_schemas, _matches
from researchhelper.planning import input_fingerprint
from tests.test_official_release_channel import CATALOG, ORIGINAL


class OfficialSelectionReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='official-reference-', dir=ROOT / 'result/acceptance')
        self.addCleanup(temporary.cleanup)
        self.agent = ResearchAgent(ResearchArchive(temporary.name), ROOT, offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create('研究国家统计局制造业PMI供需变化', cutoff='2026-10-16T18:00:00+08:00')
        self.collector = EvidenceCollector(offline=False)
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('External calls forbidden'))
        guard.start()
        self.addCleanup(guard.stop)

    def catalogue(self, payload=CATALOG, page='0'):
        with patch('researchhelper.source_transport.official_html', return_value=payload):
            record = self.collector.external(self.task, 'official', 'release_catalog',
                dict(channel='nbs_release', page=page), {})
        self.task['source_records'].append(record)
        return record

    def request(self, parameters):
        request = dict(id='read_original', source='official', operation='release_original',
            parameters=parameters, question_ids=['q1'], selection_reason='核对已留存统计局原文')
        if self.task.get('acquisition_reading_intent_version') == 1:
            request['reading_queries'] = []
        return request

    def tool(self):
        return next(item for item in data_request_contract(self.task)['tools']
                    if item['source'] == 'official' and item['operation'] == 'release_original')

    def test_new_tasks_bind_reference_policy_to_immutable_input(self):
        self.assertEqual(self.task['official_selection_reference_version'], 1)
        self.assertEqual(self.task['input_hash'], input_fingerprint(self.task))
        old = copy.deepcopy(self.task)
        old.pop('official_selection_reference_version')
        self.assertNotEqual(input_fingerprint(old), self.task['input_hash'])

    def test_no_catalogue_means_no_callable_original_selection(self):
        self.assertEqual(self.tool()['available_parameter_sets'], [])
        schemas = _planned_request_schemas(self.task)
        self.assertFalse(any(_matches(self.request({'selection_ref': 'made_up'}), item) for item in schemas))

    def test_reference_resolves_exact_pair_without_mutating_ledger_or_task(self):
        record = self.catalogue()
        before = copy.deepcopy(self.task)
        choice = selection_references(self.task)[0]
        parameters = dict(selection_ref=choice['selection_ref'])
        self.assertEqual(validate_source_request(self.task, 'official', 'release_original', parameters),
            dict(catalog_id=record['id'], release_id=record['snapshot']['entries'][0]['id']))
        self.assertEqual(validate_request(self.task, 'release_original', parameters)['url'],
            record['snapshot']['entries'][0]['url'])
        self.assertEqual(self.task, before)

    def test_model_shape_only_accepts_one_available_reference(self):
        self.catalogue()
        choice = selection_references(self.task)[0]
        tool = self.tool()
        self.assertEqual(tool['parameter_keys'], ['selection_ref'])
        self.assertEqual(set(tool['available_selections'][0]), {'selection_ref', 'title'})
        schemas = _planned_request_schemas(self.task)
        valid = self.request(dict(selection_ref=choice['selection_ref']))
        self.assertTrue(any(_matches(valid, item) for item in schemas))
        for parameters in (dict(selection_ref='forged'),
                dict(selection_ref=choice['selection_ref'], catalog_id=choice['catalog_id']),
                dict(catalog_id=choice['catalog_id'], release_id=choice['release_id'])):
            self.assertFalse(any(_matches(self.request(parameters), item) for item in schemas))

    def test_legacy_tasks_keep_pair_protocol_and_reject_references(self):
        self.catalogue()
        choice = selection_references(self.task)[0]
        self.task.pop('official_selection_reference_version')
        self.assertEqual(self.tool()['parameter_keys'], ['catalog_id', 'release_id'])
        self.assertEqual(selection_references(self.task), [])
        with self.assertRaises(ContractError):
            validate_request(self.task, 'release_original', dict(selection_ref=choice['selection_ref']))
        pair = {key: choice[key] for key in ('catalog_id', 'release_id')}
        self.assertTrue(validate_request(self.task, 'release_original', pair)['url'])

    def test_cross_task_version_scope_or_cutoff_references_are_rejected(self):
        self.catalogue()
        parameters = dict(selection_ref=selection_references(self.task)[0]['selection_ref'])
        for key, value in (('id', 'foreign'), ('version', 2), ('question', '改变主问题'),
                ('objects', ['其他对象']), ('intent', '比较对象'),
                ('cutoff', '2026-10-17T18:00:00+08:00')):
            changed = copy.deepcopy(self.task)
            changed[key] = value
            with self.subTest(field=key), self.assertRaises(ContractError):
                validate_request(changed, 'release_original', parameters)

    def test_adopting_a_plan_does_not_invalidate_its_selected_reference(self):
        self.catalogue()
        choice = selection_references(self.task)[0]
        parameters = dict(selection_ref=choice['selection_ref'])
        old_hash = self.task['input_hash']
        self.task['data_need_specs'] = [self.request(parameters)]
        self.task['input_hash'] = input_fingerprint(self.task)
        self.assertNotEqual(self.task['input_hash'], old_hash)
        self.assertEqual(selection_references(self.task)[0], choice)
        self.assertEqual(validate_source_request(self.task, 'official', 'release_original', parameters),
            {key: choice[key] for key in ('catalog_id', 'release_id')})

    def test_rehashed_fake_catalogue_never_grants_reference_authority(self):
        record = self.catalogue()
        parameters = dict(selection_ref=selection_references(self.task)[0]['selection_ref'])
        record['snapshot']['entries'][0]['title'] = '伪造标题'
        record['hash'] = fingerprint(record['snapshot'])
        self.assertEqual(selection_references(self.task), [])
        with self.assertRaises(ContractError):
            validate_source_request(self.task, 'official', 'release_original', parameters)

    def test_appending_a_page_does_not_renumber_existing_references(self):
        self.catalogue()
        original = selection_references(self.task)[0]
        payload = CATALOG.replace(b'1965449', b'1965450')
        self.catalogue(payload, page='1')
        self.assertEqual(selection_references(self.task)[0], original)

    def test_preflight_normalizes_and_detects_duplicate_alias_formats(self):
        self.catalogue()
        choice = selection_references(self.task)[0]
        reference = self.request(dict(selection_ref=choice['selection_ref']))
        pair = {key: choice[key] for key in ('catalog_id', 'release_id')}
        validated = self.agent._validated_data_needs(self.task, [reference])
        self.assertEqual(validated[0][3], pair)
        duplicate = self.request(pair)
        duplicate['id'] = 'same_original_again'
        with self.assertRaises(ContractError):
            self.agent._validated_data_needs(self.task, [reference, duplicate])

    def test_download_saves_canonical_provenance_and_no_approval(self):
        self.catalogue()
        choice = selection_references(self.task)[0]
        with patch('researchhelper.source_transport.official_html', return_value=ORIGINAL) as download:
            record = self.collector.external(self.task, 'official', 'release_original',
                dict(selection_ref=choice['selection_ref']), {})
        download.assert_called_once()
        self.assertEqual(record['parameters'], {key: choice[key] for key in ('catalog_id', 'release_id')})
        validate_original(self.task, record)
        self.assertEqual(self.task['evidence'], [])
        self.assertEqual(record['status'], '成功待核验')

    def test_invalid_reference_rejected_before_request_budget_or_transport(self):
        before = copy.deepcopy(self.task['ledger'])
        with patch('researchhelper.source_transport.official_html') as download:
            with self.assertRaises(ContractError):
                self.collector.external(self.task, 'official', 'release_original',
                    dict(selection_ref='made_up'), {})
        download.assert_not_called()
        self.assertEqual(self.task['ledger'], before)

    def test_policy_boolean_is_not_version_one(self):
        self.catalogue()
        self.task['official_selection_reference_version'] = True
        self.assertEqual(selection_references(self.task), [])
        self.assertEqual(self.tool()['parameter_keys'], ['catalog_id', 'release_id'])

    def test_frozen_report_keeps_reference_policy_without_a_second_model(self):
        task = self.agent.create('解释价格变化与总收益的区别', questions=[dict(
            id='q1', kind='fact', question='价格变化与总收益的区别',
            answer_scope='general_definition', knowledge_needs=['rh_k_price_return_measures'])])
        done = self.agent.start(task['id'])
        self.assertEqual(done['status'], '已完成', done.get('error'))
        content = self.agent.export(task['id'])['content']
        self.assertEqual(content['official_selection_reference_version'], 1)
        self.assertIsNone(self.agent.model)

    def test_source_readiness_resolves_reference_and_never_uses_invalid_wildcard(self):
        self.catalogue()
        choice = selection_references(self.task)[0]
        parameters = dict(selection_ref=choice['selection_ref'])
        with patch('researchhelper.source_transport.official_html', return_value=ORIGINAL):
            record = self.collector.external(self.task, 'official', 'release_original', parameters, {})
        record['question_ids'] = ['q1']
        self.task['source_records'].append(record)
        self.task['data_need_specs'] = [self.request(parameters)]
        self.assertTrue(initial_acquisition_coverage(self.task)['ready'])
        self.task['data_need_specs'][0]['parameters'] = dict(selection_ref='forged')
        self.assertFalse(initial_acquisition_coverage(self.task)['ready'])
