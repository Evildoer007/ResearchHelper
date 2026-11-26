"""Desk draft to Chat context, without suppliers or model calls."""
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.draft_workflow import _plan, _write
from researchhelper.contracts import ContractError, canonical
from researchhelper.evidence import available_security_codes

PAPER=dict(title='合成研究底稿',conclusion='这是用于核对上下文的测试文本，不是金融判断。',
    logics=[dict(title='逻辑甲',analysis='分析甲',implication='判断甲',risk='反证甲'),
            dict(title='逻辑乙',analysis='分析乙的原始说明',implication='判断乙',risk='反证乙'),
            dict(title='逻辑丙',analysis='分析丙',implication='判断丙',risk='反证丙')],risks=['测试材料'])


class DraftChatContextTests(unittest.TestCase):
    def setUp(self):
        blocked=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'))
        blocked.start();self.addCleanup(blocked.stop)
        self.folder=tempfile.TemporaryDirectory(prefix='rh-draft-chat-');self.addCleanup(self.folder.cleanup)
        self.agent=ResearchAgent(ResearchArchive(self.folder.name),ROOT,offline=True,draft_first=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)

    def parent(self,paper=None):
        task=self.agent.create('研究600519.SH的合成上下文案例',objects=['600519.SH'],research_mode='current')
        return self.agent.start(task['id'],dict(draft_report=copy.deepcopy(paper or PAPER),data_requests=[]))

    def test_followup_reads_displayed_draft_and_preserves_frozen_parent(self):
        parent=self.parent();before=canonical(self.agent.archive.get(parent['id']))
        html=self.agent.archive.report(parent['id'],parent['delivery']['version'])
        child=self.agent.follow_up(parent['id'],'展开逻辑2',start=False)
        context=child['conversation_context']
        logic, = context['focus']['logics']
        self.assertEqual(logic['analysis']['text'],'分析乙的原始说明')
        self.assertNotIn('previous_answer',context)
        self.assertIn('尚未独立复核',child['messages'][-2]['content'])
        self.assertEqual(context['parent_binding']['delivery_digest'],parent['delivery']['digest'])
        self.assertEqual(canonical(self.agent.archive.get(parent['id'])),before)
        self.assertEqual(self.agent.archive.report(parent['id'],parent['delivery']['version']),html)
        for key in ('evidence','source_records','calculations','theses'):self.assertEqual(child[key],[])

    def test_long_first_argument_does_not_hide_explicit_second_argument(self):
        paper=copy.deepcopy(PAPER);paper['logics'][0]['analysis']='拉长的测试段落'*1000
        parent=self.parent(paper)
        for message in ['展开逻辑2','展开逻辑二','分析策略逻辑二']:
            child=self.agent.follow_up(parent['id'],message,start=False)
            logic, = child['conversation_context']['focus']['logics']
            self.assertEqual(logic['analysis']['text'],'分析乙的原始说明')

    def test_planner_sees_reference_numbers_and_writer_sees_the_frozen_logic(self):
        child=self.agent.follow_up(self.parent()['id'],'展开逻辑2',start=False)
        captured=[]
        class Capture:
            def reason(self,task,messages,**options):
                captured.append((options['phase'],json.loads(messages[1]['content']),messages[0]['content']))
                return '{"data_requests":[]}' if options['phase']=='draft_plan' else json.dumps(PAPER)
        engine=SimpleNamespace(model=Capture())
        _plan(engine,child,1);_write(engine,child,[])
        self.assertEqual([row[0] for row in captured],['draft_plan','draft_report'])
        _, planning, _ = captured[0]
        _, writing, rule = captured[1]
        self.assertNotIn('分析乙的原始说明', canonical(planning))
        self.assertEqual(planning['conversation_context']['focus'],
                         dict(status='located', logic_indices=[2], source=child['conversation_context']['focus']['source']))
        self.assertEqual(writing['conversation_context'], child['conversation_context'])
        self.assertEqual(writing['conversation_context']['focus']['logics'][0]['analysis']['text'], '分析乙的原始说明')
        self.assertIn('不是本版事实', rule)
        self.assertEqual(child['ledger']['used']['model_calls'],0)

    def test_stale_context_is_rejected_before_model_dispatch(self):
        child=self.agent.follow_up(self.parent()['id'],'展开逻辑2',start=False)
        child['conversation_context']['parent_binding']['task_id']='different'
        class Never:
            def reason(self,*args,**kwargs):raise AssertionError('Should not dispatch')
        for call in [lambda:_plan(SimpleNamespace(model=Never()),child,1),
                     lambda:_write(SimpleNamespace(model=Never()),child,[])]:
            with self.assertRaises(ContractError):call()

    def test_historical_followup_preserves_the_declared_cutoff(self):
        task=self.agent.create('按历史时点研究600519.SH',objects=['600519.SH'],
            cutoff='2026-06-30T20:00:00+08:00',research_mode='historical')
        parent=self.agent.start(task['id'],dict(draft_report=PAPER,data_requests=[]))
        child=self.agent.follow_up(parent['id'],'展开逻辑2',start=False)
        self.assertEqual(child['research_mode'],'historical')
        self.assertEqual(child['cutoff'],parent['cutoff'])

    def test_replacing_subject_does_not_turn_old_draft_into_new_evidence(self):
        child=self.agent.follow_up(self.parent()['id'],'改看600036.SH的风险',start=False)
        self.assertEqual(child['objects'],['600036.SH'])
        self.assertEqual(available_security_codes(child),['600036.SH'])
        self.assertEqual(child['conversation_context']['scope_relation'],'replace')
        self.assertEqual(child['evidence'],[])

    def test_explicit_economic_indicator_replaces_stock_and_survives_a_reopened_thread(self):
        from researchhelper.economic_sources import declared_economic_indicators, validate_economic_request
        from researchhelper.evidence import validate_market_request

        parent = self.parent()
        frozen = self.agent.archive.report(parent['id'], 1)
        macro = self.agent.follow_up(parent['id'],
            '逐月核对中国CPI当月同比M002826730在2026年1月至9月的读数。', start=False)
        self.assertEqual(macro['conversation_context']['scope_relation'], 'replace')
        self.assertEqual(macro['objects'], ['M002826730'])
        self.assertNotIn('600519.SH', canonical(macro['conversation_context']))
        self.assertEqual(available_security_codes(macro), [])
        reopened = ResearchAgent(ResearchArchive(self.folder.name), ROOT, offline=True, draft_first=True)
        self.addCleanup(reopened.close_runtime)
        amended = reopened.follow_up(macro['id'], '把观察区间改到2026年7月至9月。', start=False)
        child = reopened.follow_up(amended['id'], '只核对这个指标8月的读数。', start=False)
        self.assertEqual(declared_economic_indicators(child), ['M002826730'])
        self.assertEqual(available_security_codes(child), [])
        self.assertEqual(child['conversation_context']['observation_request']['text'], '只核对这个指标8月的读数')
        params = dict(indicators='M002826730', startdate='2026-08-01', enddate='2026-08-31')
        self.assertEqual(validate_economic_request(child, 'edb_service', params), ['2026-08-01', '2026-08-31'])
        market = dict(codes='600519.SH', indicators='close', startdate='2026-08-31', enddate='2026-08-31',
                      functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
        with self.assertRaisesRegex(ContractError, '扩大研究范围'):
            validate_market_request(child, 'ifind', 'cmd_history_quotation', market)
        switched_back = reopened.follow_up(child['id'], '再核对600519.SH的收盘价。', start=False)
        self.assertEqual(declared_economic_indicators(switched_back), [])
        self.assertEqual(available_security_codes(switched_back), ['600519.SH'])
        self.assertEqual(reopened.archive.report(parent['id'], 1), frozen)

    def test_economic_readout_binds_one_inherited_indicator_and_literal_period(self):
        from researchhelper.draft_sources import stated_window_requests
        from researchhelper.planning import draft_research_scope

        code = 'M002826730'
        parent = self.agent.create(f'核对中国CPI当月同比{code}在2026年9月的读数。', research_mode='current')
        messages = (
            ('只核对这个指标2026年8月份的读数。', '08-01', '08-31'),
            ('那8月呢？', '08-01', '08-31'),
            ('核对2026年8月的读数。', '08-01', '08-31'),
            ('核对8月和9月的读数。', '08-01', '09-30'),
            ('核对这个指标7月至8月的读数。', '07-01', '08-31'),
            ('按上一轮的区间核对这个指标的读数。', '09-01', '09-30'),
        )
        for message, first, last in messages:
            with self.subTest(message=message):
                child = self.agent.follow_up(parent['id'], message, start=False)
                context = child['conversation_context']
                reference = context['economic_period_reference']
                self.assertEqual((reference['code'], reference['frequency']), (code, 'M'))
                request, = stated_window_requests(child)
                self.assertEqual(request['parameters'],
                                 dict(indicators=code, startdate='2026-'+first, enddate='2026-'+last))
                self.assertEqual(request['question_ids'], ['q1'])
                self.assertEqual(draft_research_scope(child)['allowed_report_scopes'], ['fact'])
                self.assertNotIn('previous_answer', context)
                self.assertEqual(child['source_records'], [])
        other = self.agent.follow_up(parent['id'], '换看2026年8月的食品CPI同比。', start=False)
        self.assertEqual(other['objects'], [])
        self.assertEqual(other['conversation_context']['scope_relation'], 'replace')
        self.assertNotIn('economic_period_reference', other['conversation_context'])
        self.assertNotIn(code, canonical(other['conversation_context']))
        analysis = self.agent.follow_up(parent['id'], '解释这个指标8月为何变化。', start=False)
        self.assertNotIn('economic_period_reference', analysis['conversation_context'])
        self.assertEqual(analysis['objects'], [code])
        edit = self.agent.follow_up(parent['id'], '把观察区间改到2026年7月至9月。', start=False)
        reading = self.agent.follow_up(edit['id'], '只核对这个指标8月的读数。', start=False)
        request, = stated_window_requests(reading)
        self.assertEqual(request['parameters'],
                         dict(indicators=code, startdate='2026-08-01', enddate='2026-08-31'))

    def test_abbreviated_economic_period_requires_one_year_and_follows_the_newest_bound_readout(self):
        from researchhelper.draft_sources import stated_window_requests

        code = 'M002826730'
        parent = self.agent.create(f'核对{code}在2025年8月和2026年8月的读数。', research_mode='current')
        ambiguous = self.agent.follow_up(parent['id'], '核对这个指标8月的读数。', start=False)
        self.assertEqual(ambiguous['conversation_context']['economic_period_reference'], dict(code=code, windows=[]))
        self.assertFalse(stated_window_requests(ambiguous))
        august = self.agent.follow_up(ambiguous['id'], '只核对2026年8月的读数。', start=False)
        september = self.agent.follow_up(august['id'], '那9月呢？', start=False)
        request, = stated_window_requests(september)
        self.assertEqual(request['parameters'],
            dict(indicators=code, startdate='2026-09-01', enddate='2026-09-30'))

    def test_mixed_comparison_resolves_either_explicit_object_without_inheriting_the_other(self):
        from researchhelper.economic_sources import declared_economic_indicators

        for question, message, expected in (
                ('比较M002826730和600519.SH在2026年9月的走势。', '只看前者。', 'M002826730'),
                ('比较600519.SH和M002826730在2026年9月的走势。', '只看前者。', '600519.SH'),
                ('比较M002826730和G123456789在2026年9月的指标读数。', '只看后者。', 'G123456789')):
            with self.subTest(question=question):
                root = self.agent.create(question, research_mode='current')
                middle = self.agent.follow_up(root['id'], '先解释两者的口径。', start=False)
                child = self.agent.follow_up(middle['id'], message, start=False)
                self.assertEqual(child['objects'], [expected])
                available = declared_economic_indicators(child) + available_security_codes(child)
                self.assertEqual(available, [expected])
                self.assertIn('2026年9月', canonical(child['conversation_context']['comparison_requests']))

    def test_excluded_code_cannot_be_queried_or_resolved_from_the_original_message(self):
        from researchhelper.evidence import data_request_contract, identity_query_parameters, validate_source_request

        parent = self.agent.create('比较510300.SH和00700.HK的收盘价。', research_mode='current')
        question = '只看510300.SH的收盘价，不查腾讯控股00700.HK。'
        tasks = [self.agent.create(question, research_mode='current'),
                 self.agent.follow_up(parent['id'], question, start=False)]
        for task in tasks:
            with self.subTest(follow_up=bool(task['parent'])):
                self.assertEqual(task['question'], question)
                contract = data_request_contract(task, first_report=True)
                self.assertEqual(contract['callable_codes'], ['510300.SH'])
                for code in ('00700', '0700'):
                    with self.assertRaises(ContractError):
                        validate_source_request(task, 'ifind', 'get_thscode', identity_query_parameters('seccode', code))
                with self.assertRaises(ContractError):
                    validate_source_request(task, 'ifind', 'get_thscode', identity_query_parameters('secname', '腾讯控股'))
                quote = dict(codes='00700.HK', indicators='close', startdate='2026-10-29', enddate='2026-10-30',
                             functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
                with self.assertRaises(ContractError):
                    validate_source_request(task, 'ifind', 'cmd_history_quotation', quote)
                validate_source_request(task, 'ifind', 'cmd_history_quotation', dict(quote, codes='510300.SH'))
        self.assertEqual(tasks[1]['objects'], ['510300.SH'])

    def test_exclusion_narrows_a_pair_and_a_later_explicit_research_can_readd_the_subject(self):
        parent = self.agent.create('比较510300.SH和159915.SZ在2026年10月的净值。', research_mode='current')
        child = self.agent.follow_up(parent['id'], '后者不查。', start=False)
        self.assertEqual(child['objects'], ['510300.SH'])
        self.assertEqual(available_security_codes(child), ['510300.SH'])
        context = child['conversation_context']
        self.assertNotIn('previous_answer', context)
        self.assertIn('2026年10月', canonical(context['comparison_requests']))
        reopened = ResearchAgent(ResearchArchive(self.folder.name), ROOT, offline=True, draft_first=True)
        self.addCleanup(reopened.close_runtime)
        next_turn = reopened.follow_up(child['id'], '继续核对净值。', start=False)
        self.assertEqual(available_security_codes(next_turn), ['510300.SH'])
        self.assertEqual(next_turn['subject_scope']['excluded'], ['159915.SZ'])
        added = reopened.follow_up(next_turn['id'], '再加入159915.SZ比较净值。', start=False)
        self.assertEqual(available_security_codes(added), ['159915.SZ', '510300.SH'])
        self.assertNotIn('subject_scope', added)

        original_order = reopened.follow_up(parent['id'], '前者不查，只看后者的净值。', start=False)
        self.assertEqual(available_security_codes(original_order), ['159915.SZ'])
        removed = reopened.follow_up(parent['id'], '排除510300.SH和159915.SZ。', start=False)
        self.assertEqual(available_security_codes(removed), [])
        self.assertEqual(available_security_codes(reopened.follow_up(removed['id'], '继续核对净值。', start=False)), [])

    def test_exclusions_apply_to_economic_indicators_but_metric_negation_keeps_the_security(self):
        from researchhelper.economic_sources import available_economic_indicators, validate_economic_request

        task = self.agent.create('核对M002826730在2026年8月的读数，不查600519.SH，也不查G123456789。',
                                 research_mode='current')
        self.assertEqual(available_security_codes(task), [])
        self.assertEqual(available_economic_indicators(task), ['M002826730'])
        with self.assertRaises(ContractError):
            validate_economic_request(task, 'edb_service',
                dict(indicators='M002826730,G123456789', startdate='2026-08-01', enddate='2026-08-31'))
        metric_only = self.agent.create('不查600519.SH的市盈率，只核对其营业收入。', research_mode='current')
        self.assertEqual(available_security_codes(metric_only), ['600519.SH'])

    def test_an_excluded_index_cannot_reenter_via_the_official_alias(self):
        from researchhelper.evidence import validate_source_request

        task = self.agent.create('核对中证500指数和沪深300指数的成分清单，排除沪深300指数。',
                                 research_mode='current')
        with self.assertRaises(ContractError):
            validate_source_request(task, 'official', 'index_catalog', dict(index_code='000300.SH'))
        validate_source_request(task, 'official', 'index_catalog', dict(index_code='000905.SH'))

    def test_imported_quote_for_an_excluded_object_stays_out_of_report_inputs(self):
        from researchhelper.draft_sources import client_writing_context, observations
        from researchhelper.evidence import snapshot_record

        task = self.agent.create('核对510300.SH的收盘价，不查159915.SZ。', research_mode='current')
        day = '2026-10-29'
        params = dict(codes='159915.SZ', indicators='close', startdate=day, enddate=day,
                      functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
        snapshot = dict(errorcode=0, tables=[dict(thscode='159915.SZ', time=[day], table=dict(close=[1.23]))])
        original = snapshot_record(task, 'ifind', 'cmd_history_quotation', params, snapshot)
        task['source_records'].append(original)
        self.assertEqual(observations(task), [])
        inputs = client_writing_context(task, [])
        self.assertNotIn(original['id'], canonical(inputs))
        self.assertEqual(task['source_records'], [original])
        parent = self.parent()
        mixed = self.agent.follow_up(parent['id'], '把它和M002826730做对比。', start=False)
        self.assertEqual(mixed['objects'], ['600519.SH', 'M002826730'])
        self.assertEqual(mixed['conversation_context']['scope_relation'], 'compare')


    def test_etf_label_and_year_switch_subject_without_inheriting_the_old_stock(self):
        from researchhelper.market_series import series_request_shapes
        parent=self.parent();before=canonical(self.agent.archive.get(parent['id']))
        for message,code,family in [('研究ETF510300.SH2026年的净值','510300.SH','ETF'),
                                    ('研究000300.SH2026年的指数估值','000300.SH','指数'),
                                    ('研究600036.SH2026年的财务','600036.SH','个股')]:
            with self.subTest(message=message):
                child=self.agent.follow_up(parent['id'],message,start=False)
                self.assertEqual(child['objects'],[code]);self.assertEqual(available_security_codes(child),[code])
                self.assertEqual(child['conversation_context']['scope_relation'],'replace')
                profiles=series_request_shapes(child)[0]['family_shapes']
                offered=[p for p in profiles if code in p['available_codes']]
                self.assertEqual([p['family'] for p in offered],[family])
                self.assertEqual(child['ledger']['used']['data_requests'],0)
                self.assertEqual(child['source_records'],[])
        self.assertEqual(canonical(self.agent.archive.get(parent['id'])),before)

    def test_pronoun_keeps_the_parent_code_attached_to_etf_label_and_year(self):
        parent=self.agent.create('研究ETF510300.SH2026年的净值',research_mode='current')
        child=self.agent.follow_up(parent['id'],'它的风险呢',start=False)
        self.assertEqual(child['objects'],['510300.SH'])
        self.assertEqual(available_security_codes(child),['510300.SH'])
        self.assertEqual(child['conversation_context']['scope_relation'],'continue')
        self.assertEqual(child['original_input']['objects'],child['objects'])

    def test_comparison_adds_only_the_explicit_new_codes_in_user_order(self):
        child=self.agent.follow_up(self.parent()['id'],'比较它与ETF159915.SZ2026年以及ETF510300.SH2026年的表现',start=False)
        self.assertEqual(child['objects'],['600519.SH','159915.SZ','510300.SH'])
        self.assertEqual(child['conversation_context']['scope_relation'],'compare')
        self.assertEqual(available_security_codes(child),['159915.SZ','510300.SH','600519.SH'])
        self.assertEqual(child['evidence'],[])

    def test_comparison_referent_preserves_the_order_in_the_question_after_reopen(self):
        parent = self.agent.create('比较600519.SH与ETF510300.SH的市场表现',
                                   objects=['510300.SH', '600519.SH'], research_mode='current')
        parent = self.agent.start(parent['id'], dict(draft_report=copy.deepcopy(PAPER), data_requests=[]))
        neutral = self.agent.follow_up(parent['id'], '补充一下两只的差异', start=False)
        reopened = ResearchAgent(ResearchArchive(self.folder.name), ROOT, offline=True,
                                 draft_first=True, require_argument_support=True)
        self.addCleanup(reopened.close_runtime)
        for message, code, other in [('核对前者的估值', '600519.SH', '510300.SH'),
                                     ('那只看后者的净值呢？', '510300.SH', '600519.SH')]:
            with self.subTest(message=message):
                child = reopened.follow_up(neutral['id'], message, start=False)
                self.assertEqual(child['objects'], [code])
                self.assertEqual(available_security_codes(child), [code])
                self.assertEqual(child['original_input']['objects'], [code])
                self.assertIn(code, child['conversation_context']['topic']['text'])
                self.assertNotIn(other, canonical(child['conversation_context']))
                self.assertEqual(child['source_records'], [])
                self.assertEqual(child['evidence'], [])
                subsequent = reopened.follow_up(child['id'], '它的主要风险呢？', start=False)
                self.assertEqual(available_security_codes(subsequent), [code])
        self.assertEqual(reopened.archive.report(parent['id'], 1)['digest'], parent['delivery']['digest'])

    def test_comparing_the_latter_to_a_new_code_excludes_the_former(self):
        parent = self.parent()
        comparison = self.agent.follow_up(parent['id'], '比较它与ETF510300.SH的表现', start=False)
        neutral = self.agent.follow_up(comparison['id'], '进一步说明差异', start=False)
        child = self.agent.follow_up(neutral['id'], '把后者和159915.SZ对比一下', start=False)
        self.assertEqual(child['objects'], ['510300.SH', '159915.SZ'])
        self.assertEqual(child['conversation_context']['scope_relation'], 'compare')
        self.assertNotIn('600519.SH', canonical(child['conversation_context']))
        later = self.agent.follow_up(child['id'], '再看前者的净值', start=False)
        self.assertEqual(available_security_codes(later), ['510300.SH'])

    def test_narrowing_a_comparison_keeps_requested_fields_and_dates_in_both_phases(self):
        parent = self.agent.create('比较沪深300指数000300.SH与上证50指数000016.SH在2026年10月29日至30日的开盘、最高、最低和收盘价。')
        parent = self.agent.start(parent['id'], dict(draft_report=copy.deepcopy(PAPER), data_requests=[]))
        previous = self.agent.follow_up(parent['id'], '补充一下两者的差异。', start=False)
        child = self.agent.follow_up(previous['id'], '只看后者', start=False)
        self.assertEqual(child['question'], '只看后者')
        self.assertEqual(available_security_codes(child), ['000016.SH'])
        self.assertEqual(child['source_records'], [])

        payloads = []
        class Capture:
            def reason(self, task, messages, **options):
                payloads.append((json.loads(messages[1]['content']), messages[0]['content']))
                return '{"data_requests":[]}' if options['phase']=='draft_plan' else json.dumps(PAPER)

        engine = SimpleNamespace(model=Capture())
        _plan(engine, child, 1)
        _write(engine, child, [])
        for payload, instructions in payloads:
            conversation = payload['conversation_context']
            references = ' '.join(item['text'] for item in conversation['comparison_requests'])
            self.assertIn('2026年10月29日至30日的开盘、最高、最低和收盘价', references)
            self.assertNotIn('000300.SH', canonical(conversation))
            self.assertNotIn(PAPER['conclusion'], canonical(conversation))
            self.assertIn('当前明确指定的条件优先', instructions)
        subsequent = self.agent.follow_up(child['id'], '改看同一标的2026年11月2日的开盘价', start=False)
        self.assertEqual(subsequent['question'], '改看同一标的2026年11月2日的开盘价')
        self.assertEqual(available_security_codes(subsequent), ['000016.SH'])
        self.assertEqual(subsequent['conversation_context']['comparison_requests'],
                         child['conversation_context']['comparison_requests'])

        continued = self.agent.follow_up(child['id'], '它的收盘价变动呢？', start=False)
        self.assertEqual(continued['conversation_context']['comparison_requests'],
                         child['conversation_context']['comparison_requests'])
        changed = self.agent.follow_up(child['id'], '改看600519.SH', start=False)
        self.assertNotIn('comparison_requests', changed['conversation_context'])

    def test_observation_change_keeps_the_subject_and_survives_an_intervening_followup(self):
        parent = self.agent.create('比较沪深300指数000300.SH与上证50指数000016.SH在2026年10月29日至30日的收盘价。',
                                   research_mode='current')
        selected = self.agent.follow_up(parent['id'], '只看后者', start=False)
        revision = self.agent.follow_up(selected['id'], '换成2026年11月2日的收盘价', start=False)
        self.assertEqual(available_security_codes(revision), ['000016.SH'])
        self.assertEqual(revision['conversation_context']['scope_relation'], 'continue')
        intervening = self.agent.follow_up(revision['id'], '先解释一下口径', start=False)
        later = self.agent.follow_up(intervening['id'], '按刚才改过的日期看开盘价', start=False)
        self.assertEqual(available_security_codes(later), ['000016.SH'])
        self.assertEqual(later['conversation_context']['observation_request']['text'], revision['question'])
        self.assertIn('2026年10月29日', canonical(later['conversation_context']['comparison_requests']))
        captured = []

        class Capture:
            def reason(self, task, messages, **options):
                captured.append((json.loads(messages[1]['content']), messages[0]['content']))
                return '{"data_requests":[]}' if options['phase']=='draft_plan' else json.dumps(PAPER)

        engine = SimpleNamespace(model=Capture())
        _plan(engine, later, 1)
        _write(engine, later, [])
        for payload, _ in captured:
            self.assertEqual(payload['conversation_context']['observation_request']['text'], revision['question'])
            self.assertIn('当前问题明确指定的条件优先', payload['conversation_context']['rule'])
            self.assertNotIn('000300.SH', canonical(payload['conversation_context']))

        changed = self.agent.follow_up(later['id'], '换成科创50指数', start=False)
        self.assertEqual(available_security_codes(changed), [])
        self.assertNotIn('observation_request', changed['conversation_context'])

    def test_named_market_window_is_resolved_before_planning_without_carrying_parent_data(self):
        from researchhelper.draft_sources import stated_window_requests
        from researchhelper.market_calendar import stated_close_intervals

        parent = self.agent.create('核对600519.SH在2026年10月27日至30日的收盘价。', research_mode='current')
        child = self.agent.follow_up(parent['id'], '按上一轮的日期区间再核对它的收盘价和涨跌幅。', start=False)
        self.assertEqual(child['conversation_context']['market_date_reference'],
                         dict(startdate='2026-10-27', enddate='2026-10-30'))
        first, last, ident = next(iter(stated_close_intervals(child)['600519.SH']))
        self.assertEqual((first.isoformat(), last.isoformat(), ident), ('2026-10-27', '2026-10-30', 'q1'))
        requests = [row for row in stated_window_requests(child) if row['operation']=='cmd_history_quotation']
        self.assertEqual([(row['parameters']['startdate'],row['parameters']['enddate']) for row in requests],
                         [('2026-10-27','2026-10-30')])
        self.assertEqual(child['source_records'], [])
        for request in ('按上一轮的日期区间。再核对收盘价。','再核对收盘价。按上一轮的日期区间。'):
            with self.subTest(request=request):
                separate = self.agent.follow_up(parent['id'],request,start=False)
                self.assertEqual(stated_close_intervals(separate),stated_close_intervals(child))

    def test_latest_delivered_close_supplies_the_nav_date_but_no_old_value(self):
        from researchhelper.draft_sources import stated_window_requests
        from researchhelper.draft_workflow import direct_readout
        from tests.market_observation_fixtures import simulated_ifind_response

        code, selected, predecessor = '510300.SH', '2026-10-22', '2026-10-21'
        calls = []

        def collect(task, source, operation, parameters, credentials):
            self.assertEqual(source, 'ifind')
            calls.append((operation, parameters))
            if operation == 'get_trade_dates':
                payload = dict(errorcode=0, tables=dict(time=[predecessor, selected]))
            elif operation == 'cmd_history_quotation':
                payload = dict(errorcode=0, tables=[dict(thscode=code, time=[predecessor, selected],
                                                        table=dict(close=[4.185, 4.107]))])
            else:
                self.assertEqual(operation, 'date_sequence')
                indicator = parameters['indipara'][0]['indicator']
                payload = dict(errorcode=0, tables=[dict(thscode=code, time=[selected], table={indicator: [4.112]})])
            return simulated_ifind_response(task, operation, parameters, payload)

        self.agent.offline = False
        with (patch('researchhelper.engine.now', return_value='2026-10-25T20:00:00+08:00'),
              patch.object(self.agent.collector, 'external', side_effect=collect)):
            parent = self.agent.create(f'核对{code}最近可用收盘价。', entry='ETF', research_mode='current')
            parent = self.agent.start(parent['id'])
            self.assertTrue(parent.get('delivery'), parent.get('error'))
            saved = self.agent.archive.report(parent['id'], parent['version'])
            closing = saved['content']['draft']['facts']
            self.assertEqual([(row['period'], row.get('previous_close_for')) for row in closing],
                             [(selected, None), (predecessor, selected)])
            child = self.agent.follow_up(parent['id'], '沿用上一轮的日期，核对单位净值。', start=False)
            self.assertEqual(child['conversation_context']['market_date_reference'],
                             dict(startdate=selected, enddate=selected))
            self.assertEqual(child['source_records'], [])
            self.assertEqual(direct_readout(child)[0], 'market_day')
            requested, = stated_window_requests(child)
            self.assertEqual(requested['parameters']['startdate'], selected)
            self.assertEqual(requested['parameters']['enddate'], selected)
            result = self.agent.start(child['id'])
            self.assertTrue(result.get('delivery'), result.get('error'))
            self.assertEqual([(row['period'], row['value']) for row in result['draft_report']['facts']],
                             [(selected, 4.112)])
            self.assertEqual(result['ledger']['used']['model_calls'], 0)
            self.assertEqual(calls[-1], ('date_sequence', requested['parameters']))
            self.assertEqual(self.agent.archive.report(parent['id'], parent['version']), saved)

            switched = self.agent.follow_up(parent['id'], '沿用上一轮的日期，核对159915.SZ的单位净值。', start=False)
            self.assertEqual(switched['conversation_context']['market_date_reference'], {})
            self.assertFalse(stated_window_requests(switched))

    def test_followup_applies_a_labelled_date_edit_and_recognizes_both_comparison_legs(self):
        from researchhelper.market_calendar import stated_close_intervals
        from researchhelper.market_series import stated_nav_intervals

        original = self.agent.create('比较600519.SH的收盘价与510300.SH的单位净值在2026年10月19日至21日的变化。',
                                     research_mode='current')
        edited = self.agent.follow_up(original['id'], '只看前者，把区间改成2026年10月27日至30日。', start=False)
        newer = self.agent.follow_up(edited['id'], '本轮不联网，按刚才改过的区间看收盘价。', start=False)
        self.assertEqual(newer['objects'], ['600519.SH'])
        self.assertEqual(newer['conversation_context']['market_date_reference'],
                         dict(startdate='2026-10-27', enddate='2026-10-30'))
        self.assertEqual(list(stated_close_intervals(newer)), ['600519.SH'])

        other = self.agent.follow_up(original['id'], '本轮不联网，只看后者，按刚才的日期区间核对单位净值。', start=False)
        self.assertEqual(other['objects'], ['510300.SH'])
        self.assertEqual(other['conversation_context']['market_date_reference'],
                         dict(startdate='2026-10-19', enddate='2026-10-21'))
        self.assertEqual(list(stated_nav_intervals(other)), ['510300.SH'])
        self.assertFalse(stated_close_intervals(other))

    def test_two_prior_windows_leave_the_current_interval_calculation_unavailable(self):
        from researchhelper.draft_sources import observations, source_context
        from researchhelper.evidence import snapshot_record
        from researchhelper.market_calendar import market_date_reference

        parent = self.agent.create('比较600519.SH从2026年10月8日至12日与从2026年10月27日至30日的收盘价。',
                                   research_mode='current')
        child = self.agent.follow_up(parent['id'], '本轮不联网，按上一轮的区间核对收盘价。', start=False)
        self.assertEqual(market_date_reference(child), {})
        source = snapshot_record(child, 'ifind', 'cmd_history_quotation',
            dict(codes='600519.SH', indicators='close', startdate='2026-10-01', enddate='2026-10-30',
                 functionpara=dict(Currency='RMB', CPS='1', Fill='Omit')),
            dict(errorcode=0,tables=[dict(thscode='600519.SH',time=['2026-10-08','2026-10-30'],table=dict(close=[10,11]))]))
        source.update(question_ids=['q1'], data_need_ids=['planned_quote'])
        child['source_records'].append(source)
        self.agent.archive.save(child)
        self.assertEqual(observations(child), [])
        packet, = [packet for packet in source_context(child) if packet['id']==source['id']]
        self.assertEqual(json.loads(packet['content']),dict(tables=[]))
        done = self.agent.start(child['id'])
        self.assertTrue(done['delivery'])
        self.assertEqual(done['draft_report']['calculations'], [])
        self.assertEqual(done['draft_report']['conclusion'], '未能确定所指日期区间，请明确起止日期。')
        self.assertEqual(done['source_records'][0]['snapshot'],source['snapshot'])

    def test_current_dates_take_priority_and_negative_or_quoted_references_have_no_effect(self):
        from researchhelper.market_calendar import market_date_reference, stated_close_intervals

        parent = self.agent.create('核对600519.SH在2026年10月27日至30日的收盘价。', research_mode='current')
        for request in ('不要沿用上一轮的日期区间，核对最近一个交易日收盘价。',
                        '不看收盘价，按上一轮的日期区间核对经营现金流。',
                        '解释文档里的说法「按上一轮的日期区间核对收盘价」。'):
            with self.subTest(request=request):
                child = self.agent.follow_up(parent['id'], request, start=False)
                self.assertIsNone(market_date_reference(child))
        child = self.agent.follow_up(parent['id'],
            '本轮不联网，按上一轮的日期区间作为对照背景，核对2026年10月19日至21日的收盘价。', start=False)
        self.assertIsNone(market_date_reference(child))
        first, last, _ = next(iter(stated_close_intervals(child)['600519.SH']))
        self.assertEqual((first.isoformat(),last.isoformat()), ('2026-10-19','2026-10-21'))

    def test_an_observation_window_is_separate_from_the_research_cutoff(self):
        parent = self.agent.create('研究沪深300指数000300.SH的开盘价。',
                                   cutoff='2026-11-03', research_mode='historical')
        first = self.agent.follow_up(parent['id'], '行情区间改到2026年10月29日至30日', start=False)
        newest = self.agent.follow_up(first['id'], '改成2026年10月28日的开盘价', start=False)
        self.assertEqual(available_security_codes(newest), ['000300.SH'])
        self.assertEqual(newest['cutoff'], parent['cutoff'])
        self.assertEqual(newest['research_mode'], 'historical')
        self.assertEqual(newest['conversation_context']['observation_request']['text'], newest['question'])
        self.assertEqual(self.agent.archive.get(first['id'])['conversation_context']['observation_request']['text'],
                         first['question'])
        changed = self.agent.follow_up(newest['id'], '换成2026年新上市的ETF', start=False)
        self.assertEqual(available_security_codes(changed), [])
        self.assertNotIn('observation_request', changed['conversation_context'])

    def test_explicit_leg_narrows_acquisition_and_preserves_the_new_window(self):
        from researchhelper.evidence import validate_market_request

        parent = self.agent.create('比较000300.SH与000016.SH在2026年10月29日至30日的收盘价。')
        child = self.agent.follow_up(parent['id'], '000016.SH在2026年11月2日的开盘价呢？', start=False)
        self.assertEqual(available_security_codes(child), ['000016.SH'])
        self.assertNotIn('000300.SH', canonical(child['conversation_context']))
        self.assertEqual(child['conversation_context']['observation_request']['text'],
                         '000016.SH在2026年11月2日的开盘价呢')
        self.assertIn('2026年10月29日至30日', canonical(child['conversation_context']['comparison_requests']))
        params = dict(codes='000016.SH', indicators='open', startdate='2026-11-02', enddate='2026-11-02',
                      functionpara={'Currency':'RMB','CPS':'1','Fill':'Omit'})
        validate_market_request(child, 'ifind', 'cmd_history_quotation', params)
        with self.assertRaisesRegex(ContractError, '不能重复代码或扩大研究范围'):
            validate_market_request(child, 'ifind', 'cmd_history_quotation', dict(params,codes='000300.SH'))

        for message in ('再与000016.SH比较收盘价', '比较000016.SH和沪深300的收盘价', '比较两者，重点看000016.SH'):
            with self.subTest(message=message):
                joined = self.agent.follow_up(parent['id'], message, start=False)
                self.assertEqual(available_security_codes(joined), ['000016.SH','000300.SH'])

        revised = self.agent.follow_up(child['id'], '换成11月3日的收盘价。', start=False)
        self.assertEqual(revised['objects'], ['000016.SH'])
        self.assertEqual(revised['conversation_context']['observation_request']['text'], '换成11月3日的收盘价')
        named = self.agent.follow_up(revised['id'], '换成2026年新上市的ETF。', start=False)
        self.assertEqual(available_security_codes(named), [])
        self.assertNotIn('observation_request', named['conversation_context'])

    def test_explicit_new_pair_does_not_keep_the_old_subject_when_asking_about_previous_prices(self):
        child = self.agent.follow_up(self.parent()['id'],
            '比较ETF159915.SZ与ETF510300.SH之前的收盘价', start=False)
        self.assertEqual(child['objects'], ['159915.SZ', '510300.SH'])
        later = self.agent.follow_up(child['id'], '先看后者的净值', start=False)
        self.assertEqual(available_security_codes(later), ['510300.SH'])

    def test_ambiguous_or_non_security_references_keep_the_previous_scope(self):
        for question, message, codes in [
            ('比较600519.SH、510300.SH和159915.SZ的表现', '看后者的净值', ['159915.SZ', '510300.SH', '600519.SH']),
            ('比较00700.HK和0700.HK的代码写法', '只看后者', ['00700.HK', '0700.HK']),
            ('研究600519.SH并列出600036.SH的代码', '看后者', ['600036.SH', '600519.SH']),
            ('比较600519.SH和510300.SH的表现', '前者和后者分别有什么风险', ['510300.SH', '600519.SH']),
            ('比较600519.SH和510300.SH的表现', '说明价格和净值的差异，后者先说明', ['510300.SH', '600519.SH']),
        ]:
            with self.subTest(question=question, message=message):
                parent = self.agent.create(question, research_mode='current')
                child = self.agent.follow_up(parent['id'], message, start=False)
                self.assertEqual(available_security_codes(child), codes)

    def test_arbitrary_latin_identifiers_are_not_promoted_to_new_securities(self):
        parent=self.parent()
        for message in ('讨论abc510300.SH2026年的文本','讨论_ETF510300.SH的命名','编号510300.SH2026x'):
            child=self.agent.follow_up(parent['id'],message,start=False)
            self.assertEqual(child['objects'],['600519.SH'])
            self.assertEqual(available_security_codes(child),['600519.SH'])
            self.assertEqual(child['conversation_context']['scope_relation'],'continue')

    def test_new_message_uses_new_grammar_without_reinterpreting_an_old_parent(self):
        from researchhelper.planning import input_fingerprint
        from researchhelper.evidence import declared_security_codes
        parent=self.agent.create('研究ETF510300.SH2026年的净值',research_mode='current')
        parent.pop('security_declaration_encoding');parent['input_hash']=input_fingerprint(parent)
        parent=self.agent.archive.save(parent);before=canonical(parent)
        self.assertEqual(declared_security_codes(parent),[])
        child=self.agent.follow_up(parent['id'],'研究ETF159915.SZ2026年的净值',start=False)
        self.assertEqual(child['security_declaration_encoding'],2)
        self.assertEqual(child['objects'],['159915.SZ'])
        self.assertEqual(available_security_codes(child),['159915.SZ'])
        self.assertEqual(canonical(self.agent.archive.get(parent['id'])),before)

    def test_shared_lexer_preserves_literal_order_duplicates_and_legacy_boundaries(self):
        from researchhelper.evidence import security_code_mentions,declared_security_codes
        text='ETF159915.SZ2026年、ETF510300.SH2026年和ETF159915.SZ2026年'
        self.assertEqual(security_code_mentions(text,encoding=2),['159915.SZ','510300.SH','159915.SZ'])
        self.assertEqual(security_code_mentions(text,encoding=1),[])
        self.assertEqual(declared_security_codes(dict(question=text,objects=[],security_declaration_encoding=2)),['159915.SZ','510300.SH'])
        with self.assertRaises(ContractError):security_code_mentions('',encoding=True)


    def test_switched_etf_fetch_and_report_do_not_reuse_the_parent_stock(self):
        from researchhelper.market_series import series_settings,validate_series_request
        parent=self.agent.create('研究600519.SH的合成上下文案例',objects=['600519.SH'],
            cutoff='2026-11-04T18:00:00+08:00',research_mode='historical')
        parent=self.agent.start(parent['id'],dict(draft_report=copy.deepcopy(PAPER),data_requests=[]))
        before=canonical(self.agent.archive.get(parent['id']))
        child=self.agent.follow_up(parent['id'],'研究ETF510300.SH2026年的净值',start=False)
        # Date-only records for November 3 are usable after that day ends.
        params=dict(codes='510300.SH',startdate='2026-11-02',enddate='2026-11-03',
            functionpara=series_settings(child,'510300.SH'),
            indipara=[dict(indicator='ths_unit_nvnf_fund',indiparams=[''])])
        self.agent.collector.offline=False
        self.agent.source_credentials={'ifind':{'access_token':'LOCAL_SCOPE_FIXTURE_ONLY'}}
        payload=dict(errorcode=0,tables=[dict(thscode='510300.SH',time=['2026-11-02','2026-11-03'],
            table={'ths_unit_nvnf_fund':[1.0,1.05]})])
        proposal=dict(title='合成净值核对',conclusion='仅比较合成净值，不代表持有人总回报。',
            logics=[dict(title='比较实际净值观测',analysis='不补造其他日期，不沿用原个股数据。',data_refs=['D1','D2'])])
        with patch.object(self.agent.collector,'_http_json',return_value=payload) as transport:
            done=self.agent.start(child['id'],dict(data_requests=[dict(id='fund_nav',source='ifind',operation='date_sequence',
                parameters=params,question_ids=['q1'],selection_reason='当前追问指定基金的净值')],draft_report=proposal))
        self.assertTrue(done.get('delivery'),done.get('error'))
        self.assertEqual(transport.call_count,1)
        self.assertEqual(json.loads(transport.call_args.args[1].data)['codes'],'510300.SH')
        self.assertEqual({row['code'] for row in done['draft_report']['facts']},{'510300.SH'},
            done.get('draft_source_issues'))
        self.assertEqual([row['raw_value'] for row in done['draft_report']['facts']],[1.0,1.05])
        self.assertEqual(done['ledger']['used']['data_requests'],1)
        self.assertEqual(done['ledger']['used']['model_calls'],0)
        with self.assertRaises(ContractError):validate_series_request(done,'date_sequence',dict(params,codes='600519.SH'))
        self.assertEqual(canonical(self.agent.archive.get(parent['id'])),before)


if __name__=='__main__':unittest.main()
