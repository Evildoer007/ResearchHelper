"""Report-writing inputs keep usable facts and scope without workflow noise."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import canonical, fingerprint
from researchhelper.draft_sources import observations, writing_context
from researchhelper.draft_workflow import _write, _source_context
from researchhelper.drafts import normalize_report, compose, render, logic_basis
from tests.test_draft_source_types import record, task_for

FINANCIAL = ROOT/'result/acceptance/one-sentence-research-q3r0y_qo/task.json'
HK = ROOT/'result/acceptance/one-sentence-research-_kw2k9ap/task.json'


def rows(context):
    result = []
    for series in context['observations']:
        common = {key: series[key] for key in ('code','object_label','period') if key in series}
        result.extend(dict(common, **dict(zip(series['columns'], values))) for values in series['rows'])
    return result


class DraftWritingContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (FINANCIAL, HK)}
        cls.financial = json.loads(FINANCIAL.read_text())
        cls.hk = json.loads(HK.read_text())

    @classmethod
    def tearDownClass(cls):
        assert all(hashlib.sha256(path.read_bytes()).hexdigest() == sha for path, sha in cls.hashes.items())

    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No live I/O'))
        guard.start();self.addCleanup(guard.stop)

    def test_all_ten_financial_values_and_periods_survive_compaction(self):
        facts = observations(self.financial)
        compact = rows(writing_context(self.financial, facts))
        self.assertEqual(len(compact), 10)
        self.assertEqual({row['id']:(row['code'],row['period'],row['value']) for row in compact},
            {row['id']:(row['code'],row['period'],row['raw_value']) for row in facts})

    def test_known_quote_unit_is_not_replaced_by_review_state(self):
        context = writing_context(self.hk, observations(self.hk))
        self.assertEqual(context['observations'][0]['unit'], '元')
        self.assertNotIn('成功待核验', canonical(context))
        self.assertNotIn('原始取数', canonical(context))
        self.assertTrue(all('source_status' not in item for item in context['sources']))

    def test_unknown_financial_units_remain_unknown(self):
        context = writing_context(self.financial, observations(self.financial))
        self.assertTrue(all(item['unit'] == '原始单位待核对' for item in context['observations']))

    def test_300_cross_section_rows_keep_codes_names_and_exact_strings(self):
        source = record({})
        task = task_for(source)
        facts = [dict(id='D'+str(i),artifact_id=source['id'],code=f'{i:06}.SZ',object_label='样本'+str(i),
            field='weight',label='成分权重',period='2026-10-26',value=.01,raw_value='0.0100',unit='%',axis_role='cross_section')
            for i in range(1,301)]
        context = writing_context(task, facts);result=rows(context)
        self.assertEqual(len(result), 300)
        self.assertEqual(result[-1]['object_label'], '样本300')
        self.assertTrue(all(row['value']=='0.0100' and row['period']=='2026-10-26' for row in result))

    def test_missing_zero_negative_and_raw_precision_are_preserved(self):
        source=record(dict(tables=[dict(thscode='600519.SH',time=['2026-10-20','2026-10-21','2026-10-22','2026-10-23'],
            table=dict(close=[None,0,-1,'2.300000000000000001']))]))
        task=task_for(source);result=rows(writing_context(task,observations(task)))
        self.assertEqual([row['value'] for row in result],[None,0,-1,'2.300000000000000001'])

    def test_foreign_records_and_facts_are_excluded(self):
        task=copy.deepcopy(self.hk);facts=observations(task)
        task['source_records']=[dict(row,task_id='foreign') for row in task['source_records']]
        context=writing_context(task,facts)
        self.assertEqual(context['observations'],[]);self.assertEqual(context['sources'],[])

    def test_document_text_keeps_locator_and_content_scope_without_binary(self):
        source=record(dict(material=dict(name='原件',status='仅可内容核对',limitation='不能证明历史可得性',
            parts=[dict(locator='第2页',text='收入增长来自产品销量。')],original_base64='A'*10000)), 'filing_original')
        context=writing_context(task_for(source),[])
        self.assertEqual(context['documents'][0]['parts'][0]['locator'],'第2页')
        self.assertEqual(context['documents'][0]['content_scope']['status'],'仅可内容核对')
        self.assertNotIn('original_base64',canonical(context))

    def test_unmapped_text_business_columns_are_not_silently_lost(self):
        source=record(dict(tables=[dict(table={'主营业务':['研发与销售']})]),'basic_data_service')
        context=writing_context(task_for(source),[])
        self.assertIn('研发与销售',context['other_data'][0]['content'])

    def test_numeric_columns_do_not_hide_adjacent_business_text_or_release_dates(self):
        source=record(dict(tables=[dict(thscode='600519.SH',time=['2026-10-20'],
            released_at='2026-10-21',table=dict(close=[10.5],主营业务=['研发与销售']))]))
        task=task_for(source);context=writing_context(task,observations(task))
        self.assertEqual(rows(context)[0]['value'],10.5)
        self.assertIn('研发与销售',context['other_data'][0]['content'])
        self.assertIn('2026-10-21',context['other_data'][0]['content'])
        self.assertNotIn('"close"',context['other_data'][0]['content'])

    def test_same_actual_quote_context_uses_less_than_half_the_previous_characters(self):
        facts=observations(self.hk)
        previous=canonical(dict(facts=facts,sources=_source_context(self.hk)))
        compact=canonical(writing_context(self.hk,facts))
        self.assertLess(len(compact),len(previous)/2)

    def test_model_receives_compact_facts_once_and_preserves_delivery_controls(self):
        captured=[]
        class Model:
            def reason(self,task,messages,**options):
                captured.append((messages,options));return '{}'
        class Engine:model=Model()
        task=copy.deepcopy(self.hk);before=copy.deepcopy(task)
        _write(Engine(),task,observations(task))
        payload=json.loads(captured[0][0][1]['content'])
        self.assertEqual(len(rows(payload['data'])),5)
        self.assertNotIn('facts',payload)
        self.assertEqual(captured[0][1]['phase'],'draft_report')
        self.assertTrue(captured[0][1]['delivery'])
        self.assertEqual(task,before)

    def test_bad_optional_reference_never_discards_report_prose(self):
        task=copy.deepcopy(self.hk);facts=observations(task)
        value=dict(title='观察',conclusion='保留正文。',logics=[dict(title='价格路径',analysis='下跌后小幅回升。',
            data_refs=['D1','foreign','D1'],source_refs=['foreign'])])
        report=normalize_report(value,task,facts)
        self.assertEqual(report['logics'][0]['data_refs'],['D1'])
        self.assertEqual(report['logics'][0]['source_refs'],[])
        self.assertTrue(report['logics'][0]['reference_notes'])
        self.assertEqual(report['logics'][0]['analysis'],'下跌后小幅回升。')

    def test_logic_link_resolves_only_its_frozen_data_without_an_accordion(self):
        task=copy.deepcopy(self.hk);task['draft_report']['logics'][0]['data_refs']=['D1','D2']
        content=compose(task);before=copy.deepcopy(content)
        selected,sources=logic_basis(content,content['draft']['logics'][0])
        self.assertEqual([row['id'] for row in selected],['D1','D2'])
        self.assertEqual(len(sources),1)
        html=render(content,ROOT)
        self.assertIn("href='#draft-basis-1'",html)
        self.assertIn("id='report-inspector'",html)
        self.assertNotIn('<details',html)
        self.assertEqual(content,before)

    def test_rendering_does_not_resolve_a_foreign_version_source(self):
        task=copy.deepcopy(self.hk);task['draft_report']['logics'][0]['data_refs']=['D1']
        content=compose(task)
        for source in content['source_records']:source['research_version']+=1
        self.assertEqual(logic_basis(content,content['draft']['logics'][0]),([],[]))


if __name__=='__main__':unittest.main()
