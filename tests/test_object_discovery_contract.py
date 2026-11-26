"""Discovery is navigation, never automatic membership or evidence approval."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, charge
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (snapshot_record, _record_current_acquisition, data_request_contract,
    available_security_codes, validate_source_request, identity_query_parameters)
from researchhelper.object_discovery import (discovery_request, discovery_response,
    retained_discovery_results, with_discovery_coverage, research_sample_codes)
from researchhelper.output_schema import _planned_request_schemas, _matches
from researchhelper.planning import input_fingerprint


class ObjectDiscoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.agent = ResearchAgent(ResearchArchive(directory.name), ROOT, offline=True,
                                   require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create('研究证券行业和算力产业链的盈利与风险', research_mode='current')
        self.params = dict(searchstring='证券行业股票', searchtype='stock')
        self.payload = dict(errorcode=0, tables=[dict(table={
            '股票代码': ['600001.SH', '000001.SZ'],
            '股票简称': ['测试样本甲', '测试样本乙'], '所属同花顺行业': ['未核验行业', '未核验行业']})])

    def retained(self):
        source = snapshot_record(self.task, 'ifind', 'smart_stock_picking', self.params, self.payload)
        charge(self.task['ledger'], 'data_requests')
        event = dict(kind='data_requests', source='ifind', operation='smart_stock_picking',
            fingerprint=source['request_hash'], status='成功', batch=self.task['batch'],
            transport_isolated=True, network_request_started=True, network_worker_reaped=True)
        self.task['ledger']['dispatches'].append(event)
        _record_current_acquisition(self.task, source, event)
        source['question_ids'] = ['q1']
        self.task['source_records'].append(source)
        return source

    def test_new_policy_and_hash(self):
        self.assertEqual(self.task['object_discovery_version'], 2)
        old = copy.deepcopy(self.task)
        old.pop('object_discovery_version')
        self.assertNotEqual(input_fingerprint(old), input_fingerprint(self.task))

    def test_explicit_industry_and_chain_templates(self):
        for query in ('证券行业股票', '算力产业链股票'):
            request = dict(searchstring=query, searchtype='stock')
            self.assertEqual(validate_source_request(self.task, 'ifind', 'smart_stock_picking', request), request)

    def test_literal_one_character_commodity_topic_not_excluded(self):
        task = self.agent.create('研究锂产业链', research_mode='current')
        self.assertEqual(discovery_request(task, dict(searchstring='锂产业链股票', searchtype='stock'))['topic'], '锂')

    def test_legacy_and_historical_tasks_not_upgraded(self):
        for change in (dict(object_discovery_version=0), dict(research_mode='historical')):
            task = dict(self.task, **change)
            with self.assertRaises(ContractError):
                validate_source_request(task, 'ifind', 'smart_stock_picking', self.params)

    def test_unknown_topic_arbitrary_conditions_or_scan_not_allowed(self):
        for query in ('医药行业股票', '证券行业股票涨幅前十', '全市场行业股票', '证券行业股票;买入', ''):
            with self.subTest(query=query), self.assertRaises(ContractError):
                validate_source_request(self.task, 'ifind', 'smart_stock_picking',
                    dict(searchstring=query, searchtype='stock'))

    def test_searchtype_and_extra_parameters_rejected(self):
        for params in (dict(self.params, searchtype='fund'), dict(self.params, page=2)):
            with self.assertRaises(ContractError):
                validate_source_request(self.task, 'ifind', 'smart_stock_picking', params)

    def test_bound_full_codes_only_grant_retrieval_scope_not_user_declaration(self):
        self.retained()
        self.assertEqual(len(retained_discovery_results(self.task)[0]['candidates']), 2)
        self.assertEqual(available_security_codes(self.task), ['000001.SZ', '600001.SH'])
        self.assertNotIn('600001.SH', self.task['objects'])
        contract = self.task['source_records'][0]['financial_query_contract']
        self.assertFalse(contract['industry_membership_verified'])
        self.assertFalse(contract['numeric_evidence_registered'])
        self.assertEqual(contract['provider_completeness'], '未证实')

    def test_candidate_permits_only_exact_identity_lookup(self):
        self.retained()
        for mode, value in (('secname', '测试样本甲'), ('seccode', '600001')):
            validate_source_request(self.task, 'ifind', 'get_thscode', identity_query_parameters(mode, value))
        for mode, value in (('secname', '测试样本'), ('seccode', '600002')):
            with self.assertRaises(ContractError):
                validate_source_request(self.task, 'ifind', 'get_thscode', identity_query_parameters(mode, value))

    def test_candidate_cannot_bypass_mapping_to_price(self):
        self.task['object_discovery_version'] = 1
        self.retained()
        with self.assertRaises(ContractError):
            validate_source_request(self.task, 'ifind', 'cmd_history_quotation', dict(codes='600001.SH',
                indicators='close', startdate='2026-09-30', enddate='2026-09-30',
                functionpara=dict(Currency='RMB', CPS='1', Fill='Omit')))

    def test_new_source_bound_code_can_be_selected_for_data_but_not_direct_evidence(self):
        self.retained()
        params = dict(codes='600001.SH', indicators='close', startdate='2026-09-30', enddate='2026-09-30',
                      functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
        validate_source_request(self.task, 'ifind', 'cmd_history_quotation', params)
        from researchhelper.evidence import direct_code_reference, declared_security_codes
        raw = snapshot_record(self.task, 'ifind', 'cmd_history_quotation', params, dict(errorcode=0, tables=[]))
        self.assertEqual(declared_security_codes(self.task), [])
        self.assertIsNone(direct_code_reference(self.task, raw))

    def test_unsigned_import_and_forged_candidate_metadata_do_not_authorize(self):
        source = self.retained()
        source.pop('acquisition')
        source['financial_query_contract']['candidates'].append(dict(code='600002.SH', name='伪造名字'))
        self.assertEqual(retained_discovery_results(self.task), [])
        with self.assertRaises(ContractError):
            validate_source_request(self.task, 'ifind', 'get_thscode', identity_query_parameters('secname', '伪造名字'))

    def test_metadata_never_replaces_raw_derivation(self):
        source = self.retained()
        source['financial_query_contract']['candidates'] = [dict(code='600002.SH', name='伪造名字')]
        results = retained_discovery_results(self.task)
        self.assertEqual([row['code'] for row in results[0]['candidates']], ['600001.SH', '000001.SZ'])

    def test_conflicting_names_across_queries_do_not_widen_code_scope(self):
        self.retained()
        self.params = dict(searchstring='算力产业链股票', searchtype='stock')
        self.payload['tables'][0]['table']['股票简称'][0] = '冲突样本名'
        self.retained()
        self.assertEqual(available_security_codes(self.task), ['000001.SZ'])

    def test_foreign_task_version_or_changed_snapshot_rejected(self):
        for key, value in (('task_id', 'foreign'), ('research_version', 999), ('hash', '0' * 64)):
            self.task['source_records'] = []
            source = self.retained()
            source[key] = value
            self.assertEqual(retained_discovery_results(self.task), [])

    def test_unregistered_market_retained_but_not_opened(self):
        self.payload['tables'][0]['table'] = {'股票代码': ['920493.BJ'], '股票简称': ['北交样本']}
        row = discovery_response(self.task, self.params, self.payload)
        self.assertEqual(row['candidates'], [])
        self.assertEqual(row['unsupported_candidates'][0]['code'], '920493.BJ')
        self.assertEqual(row['row_count'], 1)

    def test_misaligned_ambiguous_name_and_bare_codes_rejected(self):
        for fields in ({'股票代码': ['600001.SH'], '股票简称': []},
                       {'股票代码': ['600001.SH', '600001.SH'], '股票简称': ['甲', '乙']},
                       {'股票代码': ['600001'], '股票简称': ['甲']}):
            payload = dict(errorcode=0, tables=[dict(table=fields)])
            with self.assertRaises(ContractError):
                discovery_response(self.task, self.params, payload)

    def test_duplicate_same_code_preserves_all_row_positions(self):
        payload = dict(errorcode=0, tables=[dict(table={
            '股票代码': ['600001.SH', '600001.SH'], '股票简称': ['测试样本甲', '测试样本甲']})])
        row = discovery_response(self.task, self.params, payload)
        self.assertEqual(row['returned_unique_codes'], 1)
        self.assertEqual(len(row['candidates'][0]['row_locations']), 2)

    def test_contract_and_model_request_schema(self):
        self.retained()
        contract = data_request_contract(self.task)
        tool = next(row for row in contract['tools']
                    if row['operation'] == 'smart_stock_picking')
        self.assertEqual(len(contract['object_discovery']['results']), 1)
        self.assertNotIn('results', tool['object_discovery'])
        request = dict(id='discover', source='ifind', operation='smart_stock_picking',
                       parameters=self.params, question_ids=['q1'], selection_reason='确定研究候选')
        wire = copy.deepcopy(request)
        wire['parameters'] = dict(query_kind='object_discovery', topic='证券', discovery_kind='行业')
        self.assertTrue(any(_matches(wire, schema) for schema in _planned_request_schemas(self.task)))
        from researchhelper.financial_sources import semantic_query_parameters
        self.assertEqual(semantic_query_parameters(wire['parameters']), request['parameters'])
        self.assertFalse(any(_matches(request, schema) for schema in _planned_request_schemas(self.task)))

    def test_business_research_does_not_stop_at_candidate_directory(self):
        source = self.retained()
        self.task['questions'][0]['evidence_needs'] = ['本期盈利']
        coverage = with_discovery_coverage(self.task, [source], dict(ready=True))
        self.assertFalse(coverage['ready'])
        self.assertEqual(coverage['pending_discovery_question_ids'], ['q1'])

    def test_business_followup_accepts_suitable_api_not_universal_pdf(self):
        source = self.retained()
        self.task['questions'][0]['evidence_needs'] = ['本期盈利']
        data = dict(id='actual_business_data', task_id=self.task['id'], research_version=self.task['version'],
                    kind='api', source='ifind', operation='date_sequence', question_ids=['q1'])
        self.task['source_records'].append(data)
        coverage = with_discovery_coverage(self.task, [source, data], dict(ready=True))
        self.assertTrue(coverage['ready'])
        self.assertEqual(coverage['pending_discovery_question_ids'], [])

    def test_other_question_or_identity_is_not_business_followup(self):
        source = self.retained()
        self.task['questions'][0]['evidence_needs'] = ['本期盈利']
        for operation, refs in (('get_thscode', ['q1']), ('cmd_history_quotation', ['other'])):
            data = dict(id='not_business', task_id=self.task['id'], research_version=self.task['version'],
                        kind='api', source='ifind', operation=operation, question_ids=refs)
            self.task['source_records'] = [source, data]
            self.assertFalse(with_discovery_coverage(self.task, [source, data], dict(ready=True))['ready'])

    def test_explicit_query_only_does_not_force_followup(self):
        source = self.retained()
        self.task['questions'][0].update(evidence_needs=['检索返回'], method_needs=[],
                                        evidence_need_scopes={'检索返回': 'query_provenance'})
        self.assertTrue(with_discovery_coverage(self.task, [source], dict(ready=True))['ready'])

    def test_fifty_synthetic_rows_remain_incomplete_and_immutable(self):
        # Historical acceptance output is expendable. This explicit synthetic
        # fixture checks pagination semantics without claiming a real query.
        payload = dict(errorcode=0, tables=[dict(table={
            '股票代码': ['600' + str(index).zfill(3) + '.SH' for index in range(1, 51)],
            '股票简称': ['合成验收样本' + str(index) for index in range(1, 51)]})])
        with tempfile.TemporaryDirectory(prefix='synthetic-discovery-') as directory:
            path = Path(directory) / 'synthetic-response.json'
            path.write_text(json.dumps(payload, ensure_ascii=False))
            original = path.read_bytes()
            for query in ('证券行业股票', '算力产业链股票'):
                result = discovery_response(self.task, dict(searchstring=query, searchtype='stock'), json.loads(original))
                self.assertEqual(result['row_count'], 50)
                self.assertEqual(result['provider_completeness'], '未证实')
                self.assertEqual(len(result['candidates']), 50)
                self.assertEqual(path.read_bytes(), original)

    def test_candidate_universe_is_not_the_research_calculation_sample(self):
        self.retained()
        self.assertEqual(len(available_security_codes(self.task)), 2)
        self.assertEqual(research_sample_codes(self.task, {'q1'}), [])

    def test_explicit_returned_business_request_selects_only_its_code_and_question(self):
        self.retained()
        params = dict(codes='600001.SH', indicators='close', startdate='2026-09-30', enddate='2026-09-30',
                      functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
        source = snapshot_record(self.task, 'ifind', 'cmd_history_quotation', params,
            dict(errorcode=0, tables=[dict(thscode='600001.SH', time=['2026-09-30'], table=dict(close=[10]))]))
        source['question_ids'] = ['q1']
        charge(self.task['ledger'], 'data_requests')
        event = dict(kind='data_requests', source='ifind', operation='cmd_history_quotation',
            fingerprint=source['request_hash'], status='成功', batch=self.task['batch'],
            transport_isolated=True, network_request_started=True, network_worker_reaped=True)
        self.task['ledger']['dispatches'].append(event)
        _record_current_acquisition(self.task, source, event)
        self.task['source_records'].append(source)
        self.assertEqual(research_sample_codes(self.task, {'q1'}), ['600001.SH'])
        self.assertEqual(research_sample_codes(self.task, {'other'}), [])
        self.assertEqual(self.task['evidence'], [])

    def test_declared_objects_preserved_and_old_sample_scope_not_silently_changed(self):
        self.retained()
        self.task['objects'] = ['000001.SZ']
        self.assertEqual(research_sample_codes(self.task, {'q1'}), ['000001.SZ'])
        self.task.pop('discovery_sample_scope_version')
        self.assertEqual(research_sample_codes(self.task, {'q1'}), ['000001.SZ', '600001.SH'])

    def test_offline_guard_before_charge(self):
        before = copy.deepcopy(self.task['ledger'])
        with self.assertRaisesRegex(ContractError, '离线'):
            self.agent.collector.external(self.task, 'ifind', 'smart_stock_picking', self.params, {})
        self.assertEqual(self.task['ledger'], before)
