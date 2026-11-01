"""Registered wire bindings without model/provider calls; old tests are unchanged."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from tests import ROOT
from researchhelper.draft_workflow import _serialize_request, _acquire
from researchhelper.evidence import validate_market_request
from researchhelper.market_series import validate_series_request
from researchhelper.contracts import ContractError

TASK=dict(id='t',version=1,objects=['600519.SH'],entry='个股',cutoff='2026-10-29T00:00:00+08:00',questions=[dict(id='q')])
PRICE=dict(id='p',source='ifind',operation='cmd_history_quotation',parameters=dict(codes=['600519.SH'],indicators=['close'],startdate='2026-10-28',enddate='2026-10-28',functionpara=dict(Currency='RMB',CPS='1',Fill='Omit')),question_ids=['q'])
SERIES=dict(id='s',source='ifind',operation='date_sequence',parameters=dict(codes=['600519.SH'],startdate='2026-10-28',enddate='2026-10-28',functionpara=dict(Fill='Blank'),indipara=[dict(indicator='ths_pe_ttm_stock',indiparams=[])]),question_ids=['q'])


class DraftRequestSerialization(unittest.TestCase):
    def test_quotation_lists_preserve_selected_values_and_validate(self):
        before=copy.deepcopy(PRICE);value=_serialize_request(PRICE)
        self.assertEqual(value['parameters']['codes'],'600519.SH')
        self.assertEqual(value['parameters']['indicators'],'close')
        validate_market_request(TASK,'ifind','cmd_history_quotation',value['parameters'])
        self.assertEqual(PRICE,before)

    def test_registered_series_binding_is_not_delegated_to_model(self):
        before=copy.deepcopy(SERIES);value=_serialize_request(SERIES)
        self.assertEqual(value['parameters']['indipara'][0]['indiparams'],['','100'])
        validate_series_request(TASK,'date_sequence',value['parameters'])
        self.assertEqual(SERIES,before)

    def test_equivalent_argument_number_keeps_registered_meaning(self):
        request=copy.deepcopy(SERIES);request['parameters']['indipara'][0]['indiparams']=['',100]
        value=_serialize_request(request)
        validate_series_request(TASK,'date_sequence',value['parameters'])

    def test_conflicting_arguments_and_unregistered_fields_are_not_repaired(self):
        for indicator,args in [('ths_pe_ttm_stock',['','0']),('invented_pe',[])]:
            request=copy.deepcopy(SERIES);request['parameters']['indipara'][0]=dict(indicator=indicator,indiparams=args)
            value=_serialize_request(request)
            with self.assertRaises(ContractError):validate_series_request(TASK,'date_sequence',value['parameters'])

    def test_scope_dates_and_units_are_never_inferred(self):
        for field,value in [('codes',['600036.SH']),('enddate','2026-10-30')]:
            request=copy.deepcopy(PRICE);request['parameters'][field]=value
            encoded=_serialize_request(request)
            with self.assertRaises(ContractError):validate_market_request(TASK,'ifind','cmd_history_quotation',encoded['parameters'])

    def test_financial_indicator_array_stays_an_array(self):
        request=dict(source='ifind',operation='basic_data_service',parameters=dict(codes='600519.SH',indicators=['ths_revenue_stock']))
        self.assertEqual(_serialize_request(request),request)

    def test_bad_optional_request_does_not_discard_good_independent_request(self):
        task=copy.deepcopy(TASK);accepted=[]
        def receive(task,requests):
            for request in requests:
                validate_market_request(task,'ifind','cmd_history_quotation',request['parameters'])
                accepted.append(request)
        engine=SimpleNamespace(_check_run_control=lambda _:None,_acquire_sources=receive)
        bad=copy.deepcopy(PRICE);bad['parameters']['codes']=[600519]
        with patch('researchhelper.draft_workflow._checkpoint'):
            _acquire(engine,task,[bad,PRICE])
        self.assertEqual(len(accepted),1)
        self.assertEqual(task['draft_request_serialization'][-1]['original'],PRICE)
        self.assertEqual(PRICE['parameters']['codes'],['600519.SH'])


if __name__=='__main__':unittest.main()
