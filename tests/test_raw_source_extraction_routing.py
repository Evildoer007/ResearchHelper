"""Raw source retention is not body readiness or numeric evidence approval."""
import copy
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests import ROOT
from tests import test_initial_acquisition_navigation as navigation_fixture
from researchhelper import acquisition, reasoning
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import fingerprint, charge
from researchhelper.evidence import CAPABILITIES, snapshot_record
from researchhelper.market_series import SERIES_FAMILIES, series_family, series_settings
from researchhelper.official_sources import release_choices
from tests.test_official_release_channel import CATALOG, ORIGINAL


class RawSourceExtractionRoutingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='raw-extraction-', dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        self.agent = ResearchAgent(ResearchArchive(temporary.name), ROOT, offline=True)
        self.addCleanup(self.agent.close_runtime)
        guard = patch('socket.socket.connect',side_effect=AssertionError('No networking'))
        guard.start();self.addCleanup(guard.stop)

    def task(self, code='510300.SH'):
        return self.agent.create('核对'+code+'原始序列及所需公告原文',entry='混合',objects=[code],
            cutoff='2026-09-30T18:00:00+08:00',questions=[dict(id='q1',question='核对必要原文',kind='fact',
                evidence_needs=['公告原文'])])

    def series(self,task,code):
        family = series_family(code)
        field = next(iter(SERIES_FAMILIES[family]))
        parameters = dict(codes=code,startdate='2026-09-29',enddate='2026-09-30',
            functionpara=series_settings(task,code),indipara=[dict(indicator=field,indiparams=SERIES_FAMILIES[family][field])])
        record = snapshot_record(task,'ifind','date_sequence',parameters,dict(errorcode=0,tables=[
            dict(thscode=code,time=['2026-09-30'],table={field:[4.0]})]))
        task['source_records'].append(record)
        return record

    def test_stock_etf_and_index_raw_series_do_not_count_as_extractable_body(self):
        for code in ('600519.SH','510300.SH','000300.SH'):
            with self.subTest(code=code):
                task=self.task(code);record=self.series(task,code)
                self.assertIn(record['id'],[view['id'] for view in reasoning.source_views(task)])
                self.assertEqual(reasoning.extractable_source_views(task),[])

    def test_every_raw_only_inventory_operation_is_excluded_without_hiding_raw_records(self):
        views=[dict(kind='api',id=source+'.'+entry['operation'],source=source,operation=entry['operation'])
            for source,capability in CAPABILITIES.items() for entry in capability['registered_operations'] if entry['raw_only']]
        self.assertTrue(views)
        with patch.object(reasoning,'source_views',return_value=views):
            self.assertEqual(reasoning.extractable_source_views({'source_records':copy.deepcopy(views)}),[])

    def test_unknown_api_operation_cannot_become_body_readiness(self):
        with patch.object(reasoning,'source_views',return_value=[dict(kind='api',source='ifind',operation='not_registered')]):
            self.assertEqual(reasoning.extractable_source_views({}),[])

    def test_unparsed_empty_and_blank_material_do_not_stop_navigation(self):
        for status,parts in (('受限',[]),('已解析',[]),('已解析',[dict(locator='段落1',text='  ')])):
            with self.subTest(status=status,parts=parts),patch.object(reasoning,'source_views',return_value=[dict(kind='material',status=status,parts=parts)]):
                self.assertEqual(reasoning.extractable_source_views({}),[])

    def test_readable_original_is_retained_even_alongside_raw_series(self):
        task=self.task();record=self.series(task,'510300.SH')
        self.agent.archive.save(task)
        task=self.agent.add_material(task['id'],'original.txt','离线原文，未授予金融批准。'.encode())
        before=fingerprint(task)
        views=reasoning.extractable_source_views(task)
        self.assertEqual([view['id'] for view in views],[task['materials'][0]['id']])
        self.assertEqual(len(reasoning.source_views(task)),2)
        self.assertEqual(fingerprint(task),before)
        self.assertFalse(task['evidence'] or task['calculations'] or task['theses'])
        self.assertTrue(record['series_contract']['raw_only'])

    def test_registered_price_api_is_still_available_for_independent_evidence_checks(self):
        task=self.task('600519.SH')
        params=dict(codes='600519.SH',indicators='close',startdate='2026-09-29',enddate='2026-09-30',
            functionpara=dict(Currency='RMB',CPS='1',Fill='Omit'))
        record=snapshot_record(task,'ifind','cmd_history_quotation',params,dict(errorcode=0,tables=[
            dict(thscode='600519.SH',time=['2026-09-30'],table=dict(close=[100.0]))]))
        task['source_records'].append(record)
        self.assertEqual([view['id'] for view in reasoning.extractable_source_views(task)],[record['id']])
        self.assertFalse(task['evidence'])

    def test_raw_only_task_does_not_spend_an_extraction_call(self):
        task=self.task();self.series(task,'510300.SH')
        task['host_result']=dict(evidence_specs=[])
        before=copy.deepcopy(task['ledger'])
        with patch.object(reasoning,'model_object',side_effect=AssertionError('No model extraction')) as model, \
                patch('researchhelper.engine.model_object',side_effect=AssertionError('No model extraction')) as engine_model:
            result=self.agent._extract_model_sources(task)
        self.assertEqual(result,task['host_result'])
        self.assertEqual(task['ledger'],before)
        model.assert_not_called();engine_model.assert_not_called()

    def test_raw_series_does_not_short_circuit_an_existing_required_original_request(self):
        fixture=navigation_fixture.InitialAcquisitionNavigationTests()
        self.addCleanup(fixture.doCleanups);fixture.setUp()
        actual=self.task();self.series(actual,'510300.SH')
        fixture.task.update(source_records=actual['source_records'])
        fixture.decisions=[fixture.proposal('filing_original')]
        fixture.engine._acquire_sources.side_effect=lambda task,needs:setattr(fixture,'stage',3)
        def available(task):
            return [dict(id='synthetic_readable_original')] if fixture.stage==3 else reasoning.extractable_source_views(actual)
        with patch.object(acquisition,'extractable_source_views',side_effect=available):
            fixture.run_navigation()
        fixture.engine._acquire_sources.assert_called_once()
        self.assertEqual(fixture.task['reasoning_state']['acquisition']['status'],'complete')

    def test_real_contract_navigation_can_fetch_body_after_a_retained_raw_series(self):
        agent=ResearchAgent(self.agent.archive,ROOT,offline=False)
        self.addCleanup(agent.close_runtime)
        task=agent.create('核对510300.SH原始净值记录与采购经理指数原文',entry='混合',
            objects=['510300.SH','中国采购经理指数'],cutoff='2026-09-30T18:00:00+08:00',
            questions=[dict(id='q1',question='核对官方发布正文',kind='fact',evidence_needs=['官方发布正文'])])
        raw=self.series(task,'510300.SH')
        task.update(status='运行中',batch=1,run_mode='model')
        agent.archive.save(task)
        def decision(model,current,phase,payload,fields):
            self.assertEqual(phase,'acquire')
            charge(current['ledger'],'model_calls')
            choices=release_choices(current)
            operation='release_original' if choices else 'release_catalog'
            parameters=({key:choices[0][key] for key in ('catalog_id','release_id')}
                        if choices else dict(channel='nbs_release',page='0'))
            return dict(data_requests=[dict(id='proposal',source='official',operation=operation,
                parameters=parameters,question_ids=['q1'],selection_reason='核对所需官方发布正文')],
                stop_reason=''),dict(acquisition_rules_version=3,synthetic=True)
        with patch.object(acquisition,'model_object',side_effect=decision), \
                patch('researchhelper.source_transport.official_html',side_effect=[CATALOG,ORIGINAL]):
            acquisition.complete_initial_acquisition(agent,task)
        self.assertEqual(task['reasoning_state']['acquisition']['status'],'complete',task['gaps'])
        self.assertEqual(task['ledger']['used']['data_requests'],2)
        self.assertEqual(task['ledger']['used']['model_calls'],2)
        views=reasoning.extractable_source_views(task)
        self.assertEqual(len(views),1)
        self.assertNotIn(raw['id'],[view['id'] for view in views])
        self.assertEqual(len(reasoning.source_views(task)),3)
        self.assertFalse(task['evidence'] or task['calculations'] or task['theses'])
        self.assertEqual(agent.archive.get(task['id'])['source_records'],task['source_records'])


if __name__=='__main__':
    unittest.main()
