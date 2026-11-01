"""Normal-research source routing, not live model or financial-quality QA."""
import unittest
import tempfile
from unittest.mock import patch

from tests import ROOT
from tests.test_initial_acquisition_navigation import InitialAcquisitionNavigationTests as Fixture
from tests.test_official_release_channel import CATALOG, ORIGINAL
from researchhelper import acquisition
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import DOCUMENT_CONTENT_INTENT, charge
from researchhelper.official_sources import release_choices
from researchhelper.reasoning import extractable_source_views
from researchhelper.reasoning import PHASE_RULES, ACQUISITION_RULES_VERSION


class OfficialAcquisitionNavigationTests(unittest.TestCase):
    setUp = Fixture.setUp
    proposal = staticmethod(Fixture.proposal)
    respond = Fixture.respond
    acquire = Fixture.acquire
    run_navigation = Fixture.run_navigation

    def official_task(self):
        self.task['official_channels_version'] = 1
        self.task['question'] = '研究采购经理指数最新发布及其限制'
        self.task['questions'][0]['evidence_needs'] = ['官方发布正文']
        self.tools = [dict(source='official', operation=operation)
                      for operation in ('release_catalog', 'release_original')]

    def request(self, source='official', operation='release_catalog'):
        need = self.proposal(operation)
        need['data_requests'][0].update(source=source, selection_reason='需要已登记官方正文')
        return need

    def navigate(self):
        with patch.object(acquisition, 'data_request_contract', return_value={'tools': self.tools}):
            self.run_navigation()

    def test_normal_research_reaches_original_with_same_bounded_ledger(self):
        self.official_task()
        self.decisions = [self.request(), self.request(operation='release_original')]
        def acquired(task, needs):
            self.stage = 1 if needs[0]['operation'] == 'release_catalog' else 3
            task['source_records'].append(dict(id=needs[0]['id'], kind='api'))
            task['ledger']['used']['data_requests'] += 1
        self.engine._acquire_sources.side_effect = acquired
        self.navigate()
        self.assertEqual(self.task['reasoning_state']['acquisition']['status'], 'complete')
        self.assertEqual(self.task['ledger']['used']['model_calls'], 2)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 2)
        self.assertEqual(self.task['ledger']['supplements'], 0)

    def test_public_channel_does_not_require_or_offer_missing_ifind_credentials(self):
        self.official_task()
        self.engine.source_credentials = {}
        self.tools.append(dict(source='ifind', operation='get_thscode'))
        self.decisions = [dict(data_requests=[], stop_reason='无适用原件，保留缺口')]
        self.navigate()
        payload = self.model.call_args.args[3]
        self.assertEqual({t['source'] for t in payload['data_request_contract']['tools']}, {'official'})
        self.assertEqual(payload['navigation']['allowed_requests'], self.tools[:2])

    def test_source_and_operation_pair_must_match_not_just_operation_name(self):
        self.official_task()
        self.decisions = [self.request(source='ifind')]
        self.navigate()
        self.engine._acquire_sources.assert_not_called()

    def test_tinyshare_or_unknown_source_is_not_new_automatic_fallback(self):
        for source in ('tinyshare', 'unknown'):
            with self.subTest(source=source):
                self.official_task()
                self.tools.append(dict(source=source, operation='release_catalog'))
                self.decisions = [self.request(source=source)]
                self.task.pop('reasoning_state', None)
                self.navigate()
                self.engine._acquire_sources.assert_not_called()

    def test_legacy_task_does_not_gain_official_dispatch(self):
        self.tools = [dict(source='official', operation='release_catalog')]
        self.decisions = [self.request()]
        self.navigate()
        self.engine._acquire_sources.assert_not_called()

    def test_document_review_stays_read_only_even_with_registered_public_channel(self):
        self.official_task()
        self.task['intent'] = DOCUMENT_CONTENT_INTENT
        self.navigate()
        self.model.assert_not_called()
        self.engine._acquire_sources.assert_not_called()

    def test_ifind_permission_failure_is_not_silently_bypassed(self):
        self.official_task()
        with patch.object(acquisition, 'supplement_request_blocked', return_value=True):
            self.navigate()
        self.model.assert_not_called()
        self.engine._acquire_sources.assert_not_called()

    def test_budget_exhaustion_stops_public_channel_before_model_or_data(self):
        self.official_task()
        self.task['ledger']['used']['data_requests'] = 100
        self.navigate()
        self.model.assert_not_called()
        self.engine._acquire_sources.assert_not_called()

    def test_financial_question_keeps_filing_priority_not_unrelated_public_catalogue(self):
        self.official_task()
        self.task['questions'][0]['method_needs'] = ['net_profit_margin_v1']
        self.tools.extend(dict(source='ifind', operation=op) for op in ('get_thscode', 'report_query', 'filing_original'))
        self.decisions = [dict(data_requests=[], stop_reason='需要公司披露原件')]
        self.navigate()
        payload = self.model.call_args.args[3]
        self.assertEqual({t['source'] for t in payload['data_request_contract']['tools']}, {'ifind'})

    def test_unhashable_model_source_is_rejected_as_contract_error_before_dispatch(self):
        self.official_task()
        self.decisions = [self.request(source=['official'])]
        self.navigate()
        self.engine._acquire_sources.assert_not_called()
        self.assertEqual(self.task['reasoning_state']['acquisition']['status'], 'stopped')

    def test_macro_guidance_does_not_require_security_identity(self):
        self.assertIn('宏观指标和政策概念不作证券身份解析', PHASE_RULES['acquire'])
        self.assertIn('official.release_original', PHASE_RULES['acquire'])
        self.assertEqual(ACQUISITION_RULES_VERSION, 3)


del Fixture


class OfficialAcquisitionIntegrationTests(unittest.TestCase):
    def test_real_contract_collector_parser_and_checkpoint_share_catalogue_identity(self):
        with tempfile.TemporaryDirectory(prefix='official-navigation-', dir=ROOT/'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=False)
            self.addCleanup(agent.close_runtime)
            task = agent.create('分析采购经理指数最新发布', entry='宏观', research_mode='historical',
                cutoff='2026-10-16T18:00:00+08:00', questions=[dict(id='q1', kind='fact',
                    question='核对官方发布', evidence_needs=['官方发布正文'])])
            task.update(status='运行中', batch=1, run_mode='model')
            agent.archive.save(task)
            def decision(model, current, phase, payload, fields):
                self.assertEqual(phase, 'acquire')
                charge(current['ledger'], 'model_calls')
                choices = release_choices(current)
                operation = 'release_original' if choices else 'release_catalog'
                parameters = ({key: choices[0][key] for key in ('catalog_id', 'release_id')}
                              if choices else dict(channel='nbs_release', page='0'))
                return dict(data_requests=[dict(id='proposal', source='official', operation=operation,
                    parameters=parameters, question_ids=['q1'], selection_reason='需要固定渠道发布原文')],
                    stop_reason=''), dict(acquisition_rules_version=2, synthetic=True)
            with patch.object(acquisition, 'model_object', side_effect=decision), \
                    patch('researchhelper.source_transport.official_html', side_effect=[CATALOG, ORIGINAL]):
                acquisition.complete_initial_acquisition(agent, task)
            self.assertEqual(task['reasoning_state']['acquisition']['status'], 'complete', task['gaps'])
            self.assertEqual(task['ledger']['used']['data_requests'], 2)
            self.assertEqual(task['ledger']['used']['model_calls'], 2)
            views = extractable_source_views(task)
            self.assertEqual(len(views), 1)
            self.assertEqual(task['source_records'][-1]['operation'], 'release_original')
            self.assertTrue(views[0]['parts'])
            self.assertFalse(task['evidence'] or task['calculations'] or task['theses'])
            retained = agent.archive.get(task['id'])
            self.assertEqual(retained['source_records'], task['source_records'])
