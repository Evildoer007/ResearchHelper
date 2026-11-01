"""Normal mixed Chat retains per-object fields without approving raw data."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import data_request_contract, snapshot_record, EvidenceCollector
from researchhelper.reasoning import evidence_contract
from researchhelper.market_series import (SERIES_FAMILIES, series_request_shapes, series_family,
    series_settings, validate_series_request)
from researchhelper.output_schema import _planned_request_schemas, _matches

ROOT = Path(__file__).resolve().parents[1]
CODES = {'个股':'600519.SH','ETF':'510300.SH','指数':'000300.SH'}


class ObjectSeriesRoutingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='object-series-',dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        self.agent = ResearchAgent(ResearchArchive(Path(temporary.name)/'store'),ROOT,offline=True)
        guard = patch('socket.socket.connect',side_effect=AssertionError('No external calls'))
        guard.start(); self.addCleanup(guard.stop)

    def task(self, entry='混合', codes=None):
        codes = list(CODES.values()) if codes is None else codes
        return self.agent.create('核查'+ '、'.join(codes)+'的实际数据',entry=entry,objects=codes,
            cutoff='2026-09-30T18:00:00+08:00')

    def params(self, task, code):
        family = series_family(code); name = next(iter(SERIES_FAMILIES[family]))
        return dict(codes=code,startdate='2026-09-29',enddate='2026-09-30',
            functionpara=series_settings(task,code),
            indipara=[dict(indicator=name,indiparams=copy.deepcopy(SERIES_FAMILIES[family][name]))])

    def test_same_object_exposes_same_fields_in_explicit_and_default_chat(self):
        for family,code in CODES.items():
            explicit = series_request_shapes(self.task(family,[code]))[0]
            mixed = series_request_shapes(self.task('混合',[code]))[0]
            self.assertEqual(explicit,mixed)
            self.assertEqual(mixed['raw_fields'],list(SERIES_FAMILIES[family]))

    def test_task_entry_cannot_reclassify_an_explicit_object(self):
        for entry in ('个股','ETF','指数','行业','产业链','事件','宏观','混合'):
            task=self.task(entry,['600519.SH'])
            shape=series_request_shapes(task)[0]
            self.assertEqual(shape['family_shapes'][0]['family'],'个股')
            validate_series_request(task,'date_sequence',self.params(task,'600519.SH'))

    def test_mixed_objects_use_one_tool_with_three_separate_profiles(self):
        task=self.task(); before=fingerprint(task)
        tools=data_request_contract(task)['tools']
        selected=[tool for tool in tools if tool['operation']=='date_sequence']
        self.assertEqual(len(selected),1)
        profiles={profile['family']:profile for profile in selected[0]['family_shapes']}
        self.assertEqual(set(profiles),set(CODES))
        for family,code in CODES.items():
            self.assertEqual(profiles[family]['available_codes'],[code])
            validate_series_request(task,'date_sequence',self.params(task,code))
        self.assertEqual(fingerprint(task),before)

    def test_cross_object_field_is_rejected_despite_shared_task(self):
        task=self.task(); params=self.params(task,CODES['ETF'])
        params['indipara']=self.params(task,CODES['个股'])['indipara']
        with self.assertRaises(ContractError): validate_series_request(task,'date_sequence',params)

    def test_index_history_settings_cannot_be_applied_to_fund_or_stock(self):
        task=self.task()
        for code in CODES.values():
            params=self.params(task,code)
            params['functionpara']=({'Fill':'Blank'} if series_family(code)=='指数'
                                    else {'Fill':'Blank','block':'history'})
            with self.assertRaises(ContractError): validate_series_request(task,'date_sequence',params)

    def test_unknown_undeclared_or_foreign_codes_never_become_callable(self):
        task=self.task()
        for code in ('600000.SH','00700.HK','123456.SH','000001.BJ'):
            params=self.params(task,'600519.SH');params['codes']=code
            with self.assertRaises(ContractError):validate_series_request(task,'date_sequence',params)

    def test_names_without_codes_offer_profiles_but_cannot_dispatch_a_guess(self):
        task=self.agent.create('研究贵州茅台和沪深300ETF',entry='混合')
        shape=series_request_shapes(task)[0]
        self.assertEqual(shape['available_codes'],[])
        self.assertTrue(all(not profile['available_codes'] for profile in shape['family_shapes']))
        with self.assertRaises(ContractError):
            validate_series_request(task,'date_sequence',self.params(task,'600519.SH'))

    def test_strict_schema_binds_code_field_and_parameters_in_the_same_branch(self):
        task=self.task(); shapes=_planned_request_schemas(task)
        def offered(params):
            item=dict(id='series_a',source='ifind',operation='date_sequence',parameters=params,
                      question_ids=['q1'],selection_reason='核对实际原值')
            return any(_matches(item,shape) for shape in shapes)
        for code in CODES.values():self.assertTrue(offered(self.params(task,code)))
        wrong=self.params(task,CODES['ETF']);wrong['indipara']=self.params(task,CODES['个股'])['indipara']
        self.assertFalse(offered(wrong))
        wrong=self.params(task,CODES['指数']);wrong['functionpara']={'Fill':'Blank'}
        self.assertFalse(offered(wrong))

    def test_retained_record_binds_actual_code_not_outer_entry(self):
        task=self.task('混合',['510300.SH']);params=self.params(task,'510300.SH')
        field=params['indipara'][0]['indicator']
        record=snapshot_record(task,'ifind','date_sequence',params,
            dict(errorcode=0,tables=[dict(thscode='510300.SH',time=['2026-09-30'],table={field:[4.0]})]))
        self.assertEqual(record['series_contract']['family'],'ETF')
        self.assertEqual(record['series_contract']['code'],'510300.SH')
        self.assertEqual(record['series_contract']['task_entry'],'混合')
        self.assertTrue(record['series_contract']['raw_only'])
        self.assertFalse(record['series_contract']['numeric_evidence_registered'])

    def test_request_window_duplicate_fields_and_argument_guards_are_unchanged(self):
        task=self.task();params=self.params(task,'600519.SH')
        for changed in (dict(enddate='2026-10-01'),dict(startdate='2025-09-01'),
                        dict(indipara=params['indipara']*2),dict(token='synthetic')):
            with self.assertRaises(ContractError):
                validate_series_request(task,'date_sequence',dict(params,**changed))

    def test_actual_provider_adapter_sends_per_object_parameters_and_charges_once(self):
        for code in CODES.values():
            task=self.task('混合',[code]);params=self.params(task,code)
            field=params['indipara'][0]['indicator']
            payload=dict(errorcode=0,tables=[dict(thscode=code,time=['2026-09-30'],table={field:[4.0]})])
            collector=EvidenceCollector(offline=False)
            with patch.object(collector,'_http_json',return_value=payload) as transport:
                record=collector.external(task,'ifind','date_sequence',params,{'access_token':'SYNTHETIC_LOCAL_ONLY'})
            self.assertEqual(transport.call_count,1)
            self.assertEqual(json.loads(transport.call_args.args[1].data),params)
            self.assertEqual(task['ledger']['used']['data_requests'],1)
            self.assertEqual(record['series_contract']['family'],series_family(code))

    def test_wrong_object_field_is_rejected_before_authentication(self):
        task=self.task();params=self.params(task,CODES['ETF'])
        params['indipara']=self.params(task,CODES['个股'])['indipara']
        collector=EvidenceCollector(offline=False)
        with patch.object(collector,'_resolve_ifind_access') as auth, \
                patch.object(collector,'_http_json') as transport:
            with self.assertRaises(ContractError):
                collector.external(task,'ifind','date_sequence',params,{'refresh_token':'SYNTHETIC_LOCAL_ONLY'})
        auth.assert_not_called();transport.assert_not_called()
        self.assertEqual(task['ledger']['used']['data_requests'],0)

    def test_raw_series_never_becomes_approved_evidence_via_routing(self):
        task=self.task('混合',['510300.SH']);params=self.params(task,'510300.SH')
        field=params['indipara'][0]['indicator']
        record=snapshot_record(task,'ifind','date_sequence',params,
            dict(errorcode=0,tables=[dict(thscode='510300.SH',time=['2026-09-30'],table={field:[4.0]})]))
        task['source_records'].append(record)
        source=next(item for item in evidence_contract(task)['sources'] if item['artifact_id']==record['id'])
        self.assertTrue(source['series_raw_only'])
        self.assertFalse(source['numeric_evidence_registered'])
        self.assertEqual(source['api_cells']['cells'],[])

