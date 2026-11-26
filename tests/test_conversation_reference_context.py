"""Reopen the real archive and inspect the requests that reach both phases."""
from copy import deepcopy
import json
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import canonical
from researchhelper.draft_workflow import _plan, _write
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import available_security_codes
from researchhelper.planning import CONVERSATION_CONTEXT_LIMIT, conversation_context, input_fingerprint
from researchhelper.reasoning import research_scope_contract


REPORT = dict(title='旧标的经营分析', conclusion='旧标的结论：渠道改善尚待复核。', logics=[
    dict(title='第一条逻辑', analysis='第一条提供方向背景。'),
    dict(title='渠道逻辑', analysis='逐项核对渠道和需求。'*360, implication='回款改善才支持销售质量提升。',
         risk='现金回款持续低于销售增速。', argument={'condition':'次季现金回款继续改善。'}),
])


class ConversationReferenceTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix='conversation-reference-', dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.archive = ResearchArchive(folder.name)
        self.agent = self.open_agent()
        guard = patch('socket.socket.connect', side_effect=AssertionError('Offline conversation replay'))
        guard.start()
        self.addCleanup(guard.stop)

    def open_agent(self):
        agent = ResearchAgent(ResearchArchive(self.archive.root), ROOT,
                              offline=True, require_argument_support=True, draft_first=True)
        self.addCleanup(agent.close_runtime)
        return agent

    def completed_parent(self, **options):
        task = self.agent.create('核对600519.SH的经营逻辑', entry='个股', objects=['600519.SH'], **options)
        saved = self.agent.start(task['id'], dict(draft_report=REPORT, data_requests=[]))
        self.assertTrue(saved.get('delivery'), saved.get('error'))
        return saved

    def request_contexts(self, task):
        captured = []

        class Recorder:
            def reason(self, task, messages, **kwargs):
                captured.append(json.loads(messages[1]['content']))
                return json.dumps(dict(data_requests=[])) if kwargs['phase'] == 'draft_plan' else json.dumps(REPORT)

        engine = SimpleNamespace(model=Recorder())
        _plan(engine, task, 1)
        _write(engine, task, [])
        return [payload['conversation_context'] for payload in captured]

    def test_numbered_logic_keeps_judgment_risk_and_condition_independent_of_analysis_length(self):
        parent = self.completed_parent()
        child = self.agent.follow_up(parent['id'], '策略逻辑2的反证和失效条件是什么？', start=False)
        context = conversation_context(child)
        focus = context['focus']
        logic, = focus['logics']
        self.assertEqual(focus['source']['delivery_digest'], parent['delivery']['digest'])
        self.assertEqual(logic['index'], 2)
        self.assertGreater(logic['analysis']['omitted_characters'], 0)
        self.assertEqual(logic['risk']['text'], REPORT['logics'][1]['risk'])
        self.assertEqual(logic['judgment']['text'], REPORT['logics'][1]['implication'])
        self.assertEqual(logic['condition']['text'], '次季现金回款继续改善。')
        self.assertLessEqual(len(canonical(context)), CONVERSATION_CONTEXT_LIMIT)
        planning, writing = self.request_contexts(child)
        self.assertEqual(writing, context)
        self.assertEqual(planning['focus'], {key: value for key, value in focus.items() if key != 'logics'})
        self.assertEqual(child['messages'][-2]['content'].count('策略逻辑2'), 1)
        self.assertIn(REPORT['logics'][1]['risk'], child['messages'][-2]['content'])
        self.assertEqual(child['ledger']['dispatches'], [])
        general = self.agent.follow_up(parent['id'], '有什么主要反证？', start=False)
        answer = conversation_context(general)['previous_answer']['text']
        self.assertIn(REPORT['logics'][1]['risk'], answer)
        self.assertNotIn('逐项核对渠道和需求。'*20, answer)

    def test_explicit_switch_replaces_working_topic_and_keeps_chat_history_readable(self):
        parent = self.completed_parent(owner_account='admin')
        child = self.agent.follow_up(parent['id'], '改看腾讯00700.HK的主要风险', start=False)
        for context in self.request_contexts(child):
            self.assertEqual(context['scope_relation'], 'replace')
            self.assertIn('00700.HK', context['topic']['text'])
            self.assertNotIn('600519.SH', canonical(context))
            self.assertNotIn('旧标的结论', canonical(context))
        self.assertEqual(available_security_codes(child), ['00700.HK'])
        self.assertIn('旧标的结论', child['messages'][-2]['content'])
        self.assertEqual(child['evidence'], [])
        later = self.agent.follow_up(child['id'], '它的主要风险触发条件呢？', start=False)
        self.assertEqual(conversation_context(later)['topic'], conversation_context(child)['topic'])
        self.assertEqual(available_security_codes(later), ['00700.HK'])

    def test_reopen_and_reference_first_round_reads_its_original_version_after_update(self):
        first = self.completed_parent(owner_account='admin')
        second = self.agent.follow_up(first['id'], '再解释主要风险。', start=False)
        self.agent.prepare_update(first['id'], '2090-01-01T00:00:00+08:00')
        reopened = self.open_agent()
        child = reopened.follow_up(second['id'], '回看第1轮第2条逻辑的判断和反证。', start=False)
        focus = conversation_context(child)['focus']
        self.assertEqual(focus['round'], 1)
        self.assertEqual(focus['source']['task_id'], first['id'])
        self.assertEqual(focus['source']['research_version'], 1)
        self.assertEqual(focus['source']['delivery_digest'], first['delivery']['digest'])
        self.assertEqual(focus['logics'][0]['risk']['text'], REPORT['logics'][1]['risk'])
        self.assertEqual(child['input_hash'], input_fingerprint(child))
        self.assertEqual(child['materials'], [])
        self.assertEqual(child['source_records'], [])
        missing = reopened.follow_up(second['id'], '看第9轮策略逻辑2。', start=False)
        self.assertEqual(conversation_context(missing)['focus']['status'], 'unavailable')
        self.assertNotIn('logics', conversation_context(missing)['focus'])

    def test_reopen_and_return_to_the_subject_of_a_frozen_round(self):
        first = self.completed_parent(owner_account='admin')
        second = self.agent.follow_up(first['id'], '改看腾讯控股00700.HK的收盘价。', start=False)
        self.agent.prepare_update(first['id'], '2090-01-01T00:00:00+08:00')
        reopened = self.open_agent()
        child = reopened.follow_up(second['id'], '切回第1轮的标的，核对收盘价。', start=False)

        context = conversation_context(child)
        self.assertEqual(available_security_codes(child), ['600519.SH'])
        self.assertEqual(child['entry'], first['entry'])
        self.assertEqual(context['parent_binding']['task_id'], second['id'])
        reference = context['subject_reference']
        self.assertEqual((reference['round'], reference['status']), (1, 'located'))
        self.assertEqual(reference['source']['research_version'], 1)
        self.assertEqual(reference['source']['input_hash'], first['input_hash'])
        self.assertNotIn('focus', context)
        self.assertNotIn('00700.HK', canonical(context))
        self.assertNotIn(REPORT['conclusion'], canonical(context))
        self.assertEqual(self.request_contexts(child), [context, context])
        for key in ('materials', 'source_records', 'evidence', 'calculations'):
            self.assertEqual(child[key], [])

        edited = reopened.follow_up(second['id'], '请切回第一轮的那家公司，观察区间改成2026年10月12日。', start=False)
        self.assertEqual(available_security_codes(edited), ['600519.SH'])
        self.assertEqual(conversation_context(edited)['observation_request']['text'], '观察区间改成2026年10月12日')
        resumed = reopened.follow_up(edited['id'], '它的估值呢？', start=False)
        self.assertEqual(available_security_codes(resumed), ['600519.SH'])
        self.assertEqual(conversation_context(resumed)['observation_request'],
                         conversation_context(edited)['observation_request'])
        quoted = reopened.follow_up(second['id'], '查看第1轮的第2条逻辑。', start=False)
        self.assertNotIn('subject_reference', conversation_context(quoted))
        self.assertEqual(conversation_context(quoted)['focus']['source']['task_id'], first['id'])

    def test_unavailable_subject_reference_does_not_take_the_current_subject(self):
        previous = self.completed_parent()
        child = self.agent.follow_up(previous['id'], '回到第9轮的标的，核对收盘价。', start=False)
        context = conversation_context(child)
        self.assertEqual(available_security_codes(child), [])
        self.assertEqual(context['subject_reference'], {'round': 9, 'status': 'unavailable'})
        self.assertNotIn('600519.SH', canonical(context))
        self.assertEqual(self.request_contexts(child), [context, context])

    def test_return_to_a_subject_can_also_reference_a_report_under_a_new_cutoff(self):
        first = self.completed_parent()
        second = self.agent.follow_up(first['id'], '改看00700.HK的主要风险。', start=False)
        child = self.agent.follow_up(second['id'],
            '沿用第1轮的标的，参考第1轮的第2条逻辑。研究截止日期：2026-06-30。', start=False)
        self.assertEqual(available_security_codes(child), ['600519.SH'])
        self.assertEqual(child['cutoff'], '2026-06-30')
        stored = conversation_context(child)
        self.assertEqual(stored['focus']['status'], 'located')
        formal = research_scope_contract(child)['conversation_context']
        self.assertEqual(formal['subject_reference']['source']['task_id'], first['id'])
        self.assertEqual(formal['focus'], {'round': 1, 'logic_indices': [2], 'status': 'historical_reference_only'})
        self.assertNotIn(REPORT['logics'][1]['risk'], canonical(formal))

    def test_reading_a_subject_specific_conclusion_does_not_switch_research_scope(self):
        first = self.completed_parent()
        second = self.agent.follow_up(first['id'], '改看00700.HK的主要风险。', start=False)
        for message in ('第1轮标的的结论是什么？', '查看第1轮基金份额的论点。'):
            with self.subTest(message=message):
                child = self.agent.follow_up(second['id'], message, start=False)
                context = conversation_context(child)
                self.assertNotIn('subject_reference', context)
                self.assertEqual(available_security_codes(child), ['00700.HK'])
                self.assertEqual(context['focus']['source']['task_id'], first['id'])

    def test_foreign_thread_is_never_used_as_an_older_round(self):
        parent = self.completed_parent(owner_account='admin')
        child = self.agent.follow_up(parent['id'], '它的主要风险呢？', start=False)
        for owner in ('admin', 'sales'):
            with self.subTest(owner=owner):
                other = self.completed_parent(owner_account=owner)
                forged = deepcopy(child)
                forged['parent'] = other['id']
                forged['conversation_parent'].update(task_id=other['id'], version=other['version'])
                forged['conversation_context']['parent_binding'].update(task_id=other['id'],
                    research_version=other['version'], input_hash=other['input_hash'], delivery_digest=other['delivery']['digest'])
                forged['input_hash'] = input_fingerprint(forged)
                _, rounds = self.agent._conversation_rounds(forged, [1], conversation_context(forged))
                self.assertEqual(rounds, {})

    def test_reopen_twelve_rounds_and_resolve_both_frozen_reports(self):
        first = previous = self.completed_parent(owner_account='admin')
        for ordinal in range(2, 13):
            follow = self.agent.follow_up(previous['id'], f'继续核对渠道变化，补充观察{ordinal}。', start=False)
            report = deepcopy(REPORT)
            report['logics'][1].update(analysis='核对当季渠道。', implication=f'第{ordinal}次判断。', risk=f'第{ordinal}次反证。')
            previous = self.agent.start(follow['id'], dict(draft_report=report, data_requests=[]))
            self.assertTrue(previous.get('delivery'), previous.get('error'))
        self.agent.prepare_update(first['id'], '2090-01-01T00:00:00+08:00')
        reopened = self.open_agent()
        child = reopened.follow_up(previous['id'], '对比第1轮和第12轮的第2条逻辑，判断和反证分别是什么？', start=False)
        context = conversation_context(reopened.archive.get(child['id']))
        self.assertEqual(context['round_number'], 13)
        self.assertNotIn('previous_answer', context)
        self.assertEqual(context['focus']['status'], 'located')
        old, recent = context['focus']['references']
        for reference, ordinal, task in ((old, 1, first), (recent, 12, previous)):
            self.assertEqual(reference['round'], ordinal)
            self.assertEqual(reference['logic_indices'], [2])
            self.assertEqual(reference['source']['task_id'], task['id'])
            self.assertEqual(reference['source']['research_version'], 1)
            self.assertEqual(reference['source']['delivery_digest'], task['delivery']['digest'])
        self.assertEqual(old['logics'][0]['risk']['text'], REPORT['logics'][1]['risk'])
        self.assertEqual(recent['logics'][0]['risk']['text'], '第12次反证。')
        planning, writing = self.request_contexts(child)
        self.assertEqual(writing, context)
        self.assertEqual([(row['round'], row['logic_indices']) for row in planning['focus']['references']],
                         [(1, [2]), (12, [2])])
        self.assertNotIn('反证', canonical(planning['focus']))
        self.assertEqual(len(child['messages']), 25)
        self.assertEqual(child['evidence'], [])

        with patch.object(reopened.archive, 'get', wraps=reopened.archive.get) as read:
            selected = reopened.follow_up(previous['id'], '比较第12轮的第1条逻辑和第11轮的第2条逻辑。', start=False)
            version_reads = [call for call in read.call_args_list if len(call.args) > 1]
        self.assertEqual(len(version_reads), 1)
        references = conversation_context(selected)['focus']['references']
        self.assertEqual([(item['round'], item['logic_indices']) for item in references], [(12, [1]), (11, [2])])

    def test_legacy_context_adopts_round_number_and_missing_reference_keeps_available_report(self):
        parent = self.completed_parent()
        second = self.agent.follow_up(parent['id'], '渠道变化是否延续？', start=False)
        old_context = second['conversation_context']
        old_context.pop('round_number')
        old_context['version'] = 2
        second['original_input']['conversation_context'] = deepcopy(old_context)
        second['input_hash'] = input_fingerprint(second)
        self.archive.save(second)
        reopened = self.open_agent()
        child = reopened.follow_up(second['id'], '对比第1轮的结论与第9轮的第2条逻辑。', start=False)
        context = conversation_context(child)
        self.assertEqual(context['round_number'], 3)
        self.assertEqual(context['focus']['status'], 'partial')
        available, missing = context['focus']['references']
        self.assertEqual(available['conclusion']['text'], REPORT['conclusion'])
        self.assertEqual(missing, {'round':9, 'logic_indices':[2], 'status':'unavailable'})
        self.assertEqual(conversation_context(reopened.archive.get(second['id'])), old_context)

        with self.archive.connect() as db:
            db.execute('DELETE FROM versions WHERE task_id=? AND version=?', (parent['id'], parent['version']))
        completed = reopened.start(child['id'], dict(draft_report=REPORT, data_requests=[]))
        compared = reopened.follow_up(completed['id'], '对比第1轮与第3轮的逻辑2。', start=False)
        lost, retained = conversation_context(compared)['focus']['references']
        self.assertEqual(lost['status'], 'unavailable')
        self.assertEqual(retained['source']['delivery_digest'], completed['delivery']['digest'])

    def test_historical_payload_keeps_the_reference_number_without_prior_report_claims(self):
        task = self.completed_parent(research_mode='historical', cutoff='2026-06-30T18:00:00+08:00')
        child = self.agent.follow_up(task['id'], '策略逻辑2的反证是什么？', start=False)
        stored = conversation_context(child)
        self.assertEqual(stored['focus']['status'], 'located')
        formal = research_scope_contract(child)['conversation_context']
        self.assertEqual(self.request_contexts(child), [formal, formal])
        self.assertEqual(formal['focus'], {'logic_indices':[2], 'status':'historical_reference_only'})
        self.assertNotIn('previous_answer', formal)
        self.assertNotIn(REPORT['logics'][1]['risk'], canonical(formal))

    def test_multiple_long_references_stay_bounded_and_historical_projection_contains_only_numbers(self):
        escaped = '\\"\n\t' * 1000
        proposal = dict(title='历史经营分析', conclusion=escaped, logics=[
            dict(title=f'逻辑{i}', analysis=escaped, implication=escaped, risk=escaped,
                 argument=dict(condition=escaped)) for i in (1, 2, 3)])
        first = self.agent.create('核对600519.SH的经营' + escaped, objects=['600519.SH'],
                                  research_mode='historical', cutoff='2026-06-30T18:00:00+08:00')
        previous = self.agent.start(first['id'], dict(draft_report=proposal, data_requests=[]))
        for ordinal in (2, 3):
            follow = self.agent.follow_up(previous['id'], f'继续分析经营逻辑{ordinal}。' + escaped, start=False)
            previous = self.agent.start(follow['id'], dict(draft_report=proposal, data_requests=[]))
            self.assertTrue(previous.get('delivery'), previous.get('error'))
        child = self.agent.follow_up(previous['id'], '对比第1轮、第2轮及第3轮的逻辑1、逻辑2、逻辑3，再核对第8轮。', start=False)
        stored = conversation_context(child)
        self.assertLessEqual(len(canonical(stored)), CONVERSATION_CONTEXT_LIMIT)
        self.assertEqual(stored['focus']['omitted_round_count'], 1)
        self.assertEqual(stored['focus']['status'], 'partial')
        for reference in stored['focus']['references']:
            self.assertEqual(reference['logic_indices'], [1, 2, 3])
            self.assertGreater(reference['logics'][2]['analysis']['omitted_characters'], 0)
        formal = research_scope_contract(child)['conversation_context']
        self.assertEqual(formal['focus'], {'status':'historical_reference_only',
            'references':[{'round':i, 'logic_indices':[1, 2, 3]} for i in (1, 2, 3)]})
        self.assertEqual(self.request_contexts(child), [formal, formal])
