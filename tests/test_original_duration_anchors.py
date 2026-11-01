"""Do not mistake the start of a half-year interval for a daily observation.

Uses the failed R74 report as immutable negative evidence. No study resumes,
no original changes and no financial approval is manufactured by this test.
"""
import copy
import gzip
import json
import unittest
from tests import ROOT
from researchhelper.contracts import ContractError
from researchhelper.planning import input_fingerprint
from researchhelper.financial_sources import (literal_interval,original_date_is_interval_boundary,
    interval_date_anchors,original_date_anchors,original_reporting_intervals,original_interval_spans)
from researchhelper.evidence import validate_current_snapshot
from researchhelper.output_schema import _original_span_choices,original_span_output_schema,decode_original_spans

POLICY='original_duration_anchor_version'


class OriginalDurationAnchorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder=ROOT/'result/acceptance/one-sentence-research-o6gksb3_'
        source=folder/'task.json'
        if source.is_file():raw=json.loads(source.read_text())
        else:
            with gzip.open(folder/'task.json.gz','rt',encoding='utf-8') as incoming:raw=json.load(incoming)
        cls.task={k:v for k,v in raw.items() if k not in ('evidence','host_result','pending_content','pending_html',
            'argument_support_reviews','reasoning_state')}
        cls.task['evidence']=[]
        cls.views=raw['reasoning_state']['reading']['bundle']['views']
        selected=next(e for e in raw['evidence'] if e['id']=='s1_revenue_summary_1')
        cls.wrong={k:copy.deepcopy(v) for k,v in selected.items() if k not in ('snapshot','artifact')}
        # Explicit-code offline fixture: do not manufacture authority from a
        # name candidate after changing the fixture's input policy binding.
        cls.task['objects']=sorted({s['directory']['issuer'] for s in cls.task['source_records']
                                    if s['operation']=='filing_original'})
        cls.task[POLICY]=1;cls.task['input_hash']=input_fingerprint(cls.task)

    def test_shared_year_is_explicit_calendar_grammar_not_a_guessed_quarter(self):
        for quote in ('2026年1月1日至6月30日','2026 年 1 月 1 日至 6 月 30 日',
                      '2026年1月1日至2026年6月30日'):
            self.assertEqual(literal_interval(quote,allow_shared_year=True),'2026-01-01/2026-06-30')
        with self.assertRaises(ContractError):literal_interval('2026年1月1日至6月30日')
        for quote in ('2026年上半年','2026年1月1日至30日','2026年12月1日至1月30日','2026年1月1日至2月30日'):
            with self.assertRaises((ContractError,ValueError)):literal_interval(quote,allow_shared_year=True)

    def test_interval_endpoint_is_not_a_standalone_observation(self):
        text='2026 年 1 月 1 日至 6 月 30 日止期间财务报表'
        self.assertTrue(original_date_is_interval_boundary(text,'2026 年 1 月 1 日','2026-01-01'))
        self.assertFalse(original_date_is_interval_boundary('报告期末2026年6月30日','2026年6月30日','2026-06-30'))
        self.assertFalse(original_date_is_interval_boundary(text,'2026年1月1日','2026-01-01'))

    def test_the_actual_wrong_daily_binding_is_rejected_without_changing_old_task_rules(self):
        with self.assertRaisesRegex(ContractError,'不能充当单日观测'):
            validate_current_snapshot(self.wrong,self.task)
        old=dict(self.task);old.pop(POLICY);old['input_hash']=input_fingerprint(old)
        self.assertTrue(validate_current_snapshot(self.wrong,old))

    def corrected(self):
        source=next(s for s in self.task['source_records'] if s['id']==self.wrong['artifact_id'])
        part=self.wrong['record_locator']['period_part']
        text=next(p['text'] for p in source['snapshot']['material']['parts'] if p['locator']==part)
        span=next(s for s in original_interval_spans(text) if s['period']=='2026-01-01/2026-06-30')
        item=copy.deepcopy(self.wrong)
        item.update(period=span['period'],observation_date='2026-06-30',reporting_period_basis=dict(
            version=3,kind='material',artifact_id=source['id'],snapshot_hash=source['hash'],part=part,quote=span['quote']))
        item['record_locator'].update(period_part=part,period_quote=span['quote'])
        return item

    def test_full_literal_duration_can_pass_the_date_gate_not_financial_approval(self):
        item=self.corrected()
        self.assertTrue(validate_current_snapshot(item,self.task))
        self.assertEqual(item['value'],self.wrong['value'])
        old=dict(self.task);old.pop(POLICY)
        with self.assertRaisesRegex(ContractError,'未登记共享年份'):validate_current_snapshot(item,old)

    def test_duration_cannot_move_its_endpoint_or_borrow_another_quote(self):
        for change in ('endpoint','quote','part','period'):
            item=self.corrected()
            if change=='endpoint':item['observation_date']='2026-01-01'
            elif change=='quote':item['record_locator']['period_quote']='2026 年 1 月 1 日'
            elif change=='part':item['reporting_period_basis']['part']='不存在的原文'
            else:item['period']='2026-01-01/2026-09-30'
            with self.assertRaises(ContractError):validate_current_snapshot(item,self.task)

    def test_selector_keeps_complete_intervals_but_excludes_the_wrong_daily_choices(self):
        choices=_original_span_choices(self.task,self.views)
        self.assertTrue(choices)
        for choice in choices:
            self.assertTrue(all(p['representation_version']==3 for p in choice['period_anchors']))
            dates=choice['description']['dates']
            self.assertTrue(all(not dates[i].get('interval_only') for i in choice['observation_indices']))
        original=next(c for c in choices if c['view']['id']==self.wrong['artifact_id'])
        self.assertTrue(any(p['period']=='2026-01-01/2026-06-30' for p in original['period_anchors']))
        locator=self.wrong['record_locator']
        bad=[i for i,d in enumerate(original['description']['dates'])
            if d['part']==locator['period_part'] and d['quote']==locator['period_quote']]
        self.assertTrue(bad)
        self.assertTrue(all(i not in original['observation_indices'] for i in bad))

    def test_duration_choices_keep_the_actual_part_and_quote(self):
        view=dict(parts=[dict(locator='期间标题',text='2026年1月1日至6月30日')])
        dates=interval_date_anchors(view,original_date_anchors(view))
        choices=original_reporting_intervals(view,dates)
        self.assertEqual(len(choices),1)
        self.assertEqual(choices[0]['period'],'2026-01-01/2026-06-30')
        self.assertEqual(dates[choices[0]['end_anchor']]['quote'],view['parts'][0]['text'])

    def test_period_selection_decodes_version_three_then_passes_the_unchanged_source_gate(self):
        choice=next(c for c in _original_span_choices(self.task,self.views)
                    if c['view']['id']==self.wrong['artifact_id'])
        period=next(p for p in choice['period_anchors'] if p['period']=='2026-01-01/2026-06-30')
        schema=original_span_output_schema(self.task,self.views)
        variant=next(v for v in schema['properties']['selections']['items']['anyOf']
            if v['properties']['source_ref']['enum']==[choice['source_ref']]
            and v['properties']['kind']['enum']==['text'])
        props=variant['properties']
        link=props['review']['properties']['need_links']['items']['anyOf'][0]['properties']
        item=dict(id='duration_codec',source_ref=choice['source_ref'],kind='text',object='600030.SH',
            nature=props['nature']['enum'][0],temporal_reference=dict(period_ref=period['ref']),
            review=dict(scope='current_original_descriptive_only',need_links=[dict(
                question_id=link['question_id']['enum'][0],need_ids=[link['need_ids']['items']['enum'][0]])]))
        line=next(row['index'] for row in choice['lines'] if row['part']==period['part'])
        if 'text_reference' in props:item['text_reference']=dict(line=line)
        else:item['text_span']=dict(start=line,end=line)
        if 'question_ids' in props:item['question_ids']=[link['question_id']['enum'][0]]
        wire=dict(version=12,binding_ref=schema['properties']['binding_ref']['enum'][0],selections=[item])
        spec=decode_original_spans(wire,schema,(self.task,self.views))['evidence_specs'][0]
        self.assertEqual(spec['reporting_period_basis']['version'],3)
        self.assertEqual(spec['period'],'2026-01-01/2026-06-30')
        self.assertTrue(validate_current_snapshot(spec,self.task))
        self.assertNotIn('applicability_check',spec)
