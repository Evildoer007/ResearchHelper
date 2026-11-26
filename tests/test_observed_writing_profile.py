"""A literal-series writing profile prevents endpoint and whole-window confusion."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import json,socket,tempfile,unittest
from unittest.mock import patch
from tests import ROOT
from core.session import Session
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import canonical,fingerprint
from researchhelper.draft_sources import observations,writing_context,observation_context
from researchhelper.draft_workflow import _write
from researchhelper.drafts import normalize_report
from researchhelper.knowledge import ResearchKnowledge,CATALOGUE_POLICY
from tests.test_draft_financial_period import sample,add_record,rehash


def source_task(values,dates=None):
    task=sample();task.update(entry='宏观',question='分析CPI变化及有条件的投资含义',objects=['中国CPI'],
        questions=[],evidence=[],calculations=[],source_records=[],draft_source_reader_version=8)
    source=add_record(task,'',(0,),operation='edb_service',fields=('value',))
    dates=dates or ['2026-01-31','2026-02-28','2026-07-31','2026-09-30'][:len(values)]
    source.update(parameters=dict(indicators='M1',startdate=min(dates),enddate=max(dates)))
    source['snapshot']['tables']=[dict(id=['M1'],time=dates,value=values)];rehash(source)
    task[CATALOGUE_POLICY]=3
    task['knowledge']=ResearchKnowledge().read('宏观',task['question'],context={CATALOGUE_POLICY:3})
    return task


def profile(task):return writing_context(task,observations(task))['observations'][0]['observed_profile']


class ObservedWritingProfileTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external I/O'))
        guard.start();self.addCleanup(guard.stop)

    def test_actual_failure_shape_uses_january_not_july_as_window_minimum(self):
        task=source_task(['0.9','0.5','1.3','0.2'],['2026-09-30','2026-07-31','2026-02-28','2026-01-31'])
        before=canonical(task);result=profile(task)
        self.assertEqual(result['minimum'],dict(data_ref='D4',period='2026-01-31',value='0.2'))
        self.assertEqual(result['maximum'],dict(data_ref='D3',period='2026-02-28',value='1.3'))
        self.assertEqual(result['first']['data_ref'],'D4');self.assertEqual(result['last']['data_ref'],'D1')
        self.assertEqual((result['start'],result['end']),('2026-01-31','2026-09-30'))
        self.assertEqual(canonical(task),before)

    def test_positive_food_months_remain_explicit_despite_negative_endpoints(self):
        task=source_task(['-0.7','1.7','0.3','-1.7'])
        result=profile(task);self.assertTrue(result['has_positive']);self.assertTrue(result['has_negative'])
        self.assertFalse(result['has_zero']);self.assertEqual(result['minimum']['period'],'2026-09-30')

    def test_missing_values_are_not_zero_and_keep_the_full_sample_window(self):
        task=source_task([None,'0','-1','2']);result=profile(task)
        self.assertEqual(result['observations'],3);self.assertEqual(result['missing'],1)
        self.assertTrue(result['has_zero']);self.assertEqual(result['first']['period'],'2026-02-28')
        self.assertEqual(result['start'],'2026-01-31');self.assertEqual(result['minimum']['value'],'-1')

    def test_decimal_ordering_does_not_collapse_raw_precision(self):
        task=source_task(['0.010000000000000001','0.010000000000000002'])
        result=profile(task);self.assertEqual(result['minimum']['data_ref'],'D1');self.assertEqual(result['maximum']['data_ref'],'D2')
        self.assertEqual(result['maximum']['value'],'0.010000000000000002')

    def test_tied_extrema_choose_an_actual_earliest_observation_without_new_values(self):
        task=source_task([2,1,1,2]);result=profile(task)
        self.assertEqual(result['minimum']['data_ref'],'D2');self.assertEqual(result['maximum']['data_ref'],'D1')
        rows={row['id']:row for row in observations(task)}
        for name in ('first','last','minimum','maximum'):
            selected=result[name];self.assertEqual(selected['value'],rows[selected['data_ref']]['raw_value'])

    def test_duplicate_or_invalid_periods_do_not_invent_a_temporal_order(self):
        for dates in (['2026-01-31','2026-01-31'],['2026-01-31','unknown']):
            task=source_task([1,2],dates)
            with self.subTest(dates=dates):
                context=writing_context(task,observations(task));self.assertNotIn('observed_profile',context['observations'][0])
                self.assertEqual(len(context['observations'][0]['rows']),2)

    def test_single_observation_has_no_range_noise(self):
        task=source_task([1]);context=writing_context(task,observations(task))
        self.assertNotIn('observed_profile',context['observations'][0]);self.assertEqual(observation_context(task,observations(task)),{})

    def test_different_objects_and_cross_sections_do_not_form_one_profile(self):
        task=source_task([1,2]);source=task['source_records'][0]
        source.update(operation='smart_stock_picking',parameters=dict(searchstring='unknown',searchtype='stock'))
        source['snapshot']['tables']=[dict(table={'股票代码':['600519.SH','002594.SZ'],'指标[20251231]':[1,2]})];rehash(source)
        self.assertEqual(len(observations(task)),2)
        self.assertTrue(all('observed_profile' not in item for item in writing_context(task,observations(task))['observations']))

    def test_quote_selection_cannot_reintroduce_future_or_unrequested_values_via_profile(self):
        task=source_task([1,2]);source=task['source_records'][0];task['draft_quote_scope_version']=2
        source.update(operation='cmd_history_quotation',parameters=dict(codes='600519.SH',indicators='close',
            startdate='2026-10-29',enddate='2026-10-30',functionpara=dict(Currency='RMB')))
        source['snapshot']['tables']=[dict(thscode='600519.SH',time=['2026-10-29','2026-10-30','2026-11-02'],table=dict(close=[10,11,99999],open=[88888]*3))];rehash(source)
        context=writing_context(task,observations(task));self.assertNotIn('99999',canonical(context));self.assertNotIn('88888',canonical(context))
        result=context['observations'][0]['observed_profile'];self.assertEqual(result['maximum']['value'],11);self.assertEqual(result['end'],'2026-10-30')

    def test_foreign_and_changed_sources_do_not_supply_profile_context(self):
        for change in ('owner','hash'):
            task=source_task([1,2]);facts=observations(task);source=task['source_records'][0]
            if change=='owner':source['task_id']='foreign'
            else:source['hash']='changed'
            with self.subTest(change=change):self.assertEqual(observation_context(task,facts),{})

    def test_new_report_freezes_detached_profile_without_rewriting_raw_facts(self):
        task=source_task([.2,1.3,.5,.9]);facts=observations(task);before=deepcopy(facts)
        report=normalize_report({},task,facts);context=report['observation_context']
        self.assertEqual(context['task_id'],task['id']);self.assertEqual(context['facts_hash'],fingerprint(facts))
        self.assertEqual(context['profiles'],observation_context(task,facts)['profiles']);self.assertEqual(facts,before)
        frozen=deepcopy(report);task['source_records'][0]['snapshot']['tables'][0]['value'][0]=.1
        self.assertEqual(report,frozen)

    def test_writer_example_uses_a_real_locked_id_and_integer_version(self):
        task=source_task([.2,1.3,.5,.9]);calls=[]
        class Model:
            def reason(self,task,messages,**options):calls.append((messages,options));return '{}'
        class Engine:model=Model()
        _write(Engine(),task,observations(task));self.assertEqual(len(calls),1)
        payload=json.loads(calls[0][0][1]['content']);example=payload['reference_examples']['knowledge_refs']
        self.assertEqual(type(example[0]['version']),int)
        accepted,notes=ResearchKnowledge().draft_references(task,example)
        self.assertEqual(len(accepted),1);self.assertEqual(notes,[])
        self.assertEqual(payload['data']['observations'][0]['observed_profile']['minimum']['value'],.2)
        self.assertEqual(calls[0][1]['phase'],'draft_report')

    def test_invalid_knowledge_references_are_still_not_approved_or_a_report_gate(self):
        task=source_task([.2,.9]);facts=observations(task)
        report=normalize_report(dict(conclusion='保留研究。',logics=[dict(title='路径',analysis='条件成立时讨论。',
            knowledge_refs=['rh_k_cpi_reading',dict(id='rh_k_cpi_reading',version='1')])]),task,facts)
        self.assertEqual(report['conclusion'],'保留研究。');self.assertEqual(report['logics'][0]['knowledge_refs'],[])
        self.assertTrue(report['logics'][0]['reference_notes'])

    def test_reference_notes_distinguish_format_version_and_unknown_entry(self):
        task=source_task([.2,.9]);knowledge=ResearchKnowledge()
        for proposal,reason in [(['rh_k_cpi_reading'],'对象'),
            ([dict(id='rh_k_cpi_reading',version='1')],'整数'),
            ([dict(id='rh_k_cpi_reading',version=2)],'版本与'),
            ([dict(id='unknown',version=1)],'尚未登记')]:
            with self.subTest(reason=reason):
                accepted,notes=knowledge.draft_references(task,proposal)
                self.assertEqual(accepted,[]);self.assertTrue(any(reason in note for note in notes))

    def test_skill_and_app_share_profile_and_reference_examples_without_network(self):
        task=source_task([.2,1.3,.5,.9]);calls=[]
        class Model:
            def reason(self,task,messages,**options):calls.append(messages);return '{}'
        class Engine:model=Model()
        with tempfile.TemporaryDirectory(dir=ROOT/'result') as folder:
            session=Session.__new__(Session);session.archive=ResearchArchive(Path(folder)/'data');session.project=Path(folder)
            from researchhelper.engine import ResearchAgent
            session.agent=ResearchAgent(session.archive,ROOT,offline=True,require_argument_support=True,draft_first=True)
            self.addCleanup(session.agent.close_runtime)
            fresh=session.agent.create(task['question'],entry='宏观',objects=task['objects'],research_mode='current')
            source=deepcopy(task['source_records'][0]);source.update(task_id=fresh['id'],research_version=fresh['version'],content_version=1)
            fresh['source_records']=[source];session.archive.save(fresh)
            fresh=session.draft(fresh['id'], data_requests=[], defer_delivery=True)
            self.assertEqual(fresh['knowledge'][CATALOGUE_POLICY],fresh[CATALOGUE_POLICY])
            _write(Engine(),fresh,observations(fresh));packet=json.loads(calls[0][1]['content'])
            skill=session.tools(fresh['id'])['first_report']
            self.assertEqual(skill['observation_profiles'],observation_context(fresh,observations(fresh))['profiles'])
            self.assertEqual(skill['reference_examples'],packet['reference_examples'])
            self.assertEqual(session.status(fresh['id'])['ledger']['used']['model_calls'],0)


if __name__=='__main__':unittest.main()
