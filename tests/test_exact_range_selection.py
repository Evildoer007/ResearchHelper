"""Raw extrema stay exact when different observations share one float value.

All inputs here are explicit synthetic boundary values. They test selection,
references, persistence and delivery, not provider permissions or model quality.
"""
from copy import deepcopy
from decimal import Decimal
from itertools import permutations
import json
from io import BytesIO
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import canonical, fingerprint
from researchhelper.draft_analysis import analyze, current_analysis
from researchhelper.draft_sources import observations, VERSION as READER_VERSION
from researchhelper.drafts import compose, normalize_report, source_binding

RESOURCES=Path(os.environ.get('RESEARCHHELPER_TEST_RESOURCES',Path(__file__).resolve().parents[1]))
VALUES=['1.000000000000000002','1.000000000000000001','1.000000000000000003']


def scenario(values=None, *, field='unit_nav'):
    values=VALUES if values is None else values
    task=dict(id='synthetic-exact-range',version=1,input_hash='synthetic-exact-range-input',
        synthetic=True,cutoff='2026-10-31T23:00:00+08:00',question='观察已取得的单位净值原值范围',
        entry='ETF',intent='解释变化',objects=['510300.SH'],source_records=[],materials=[],
        questions=[],evidence=[],calculations=[],knowledge={},draft_source_reader_version=READER_VERSION)
    dates=['2026-10-'+str(20+i) for i in range(len(values))]
    if field=='unit_nav':
        packets=[('nav-'+str(i),'official','etf_pcf',dict(ts_code='510300.SH'),
            dict(code=0,data=[dict(ts_code='510300.SH',trade_date=day,unit_nav=value)]))
            for i,(day,value) in enumerate(zip(dates,values),1)]
    else:
        packets=[('nav-series','ifind','date_sequence',dict(codes='510300.SH',functionpara={},
            startdate=dates[0],enddate=dates[-1],indipara=[dict(indicator=field,indiparams=[''])]),
            dict(errorcode=0,tables=[dict(thscode='510300.SH',time=dates,table={field:values})]))]
    for ident,source,operation,parameters,snapshot in packets:
        task['source_records'].append(dict(id=ident,kind='api',source=source,operation=operation,
            task_id=task['id'],research_version=task['version'],parameters=parameters,snapshot=snapshot,
            status='成功待核验',hash=fingerprint(snapshot),
            request_hash=fingerprint(dict(source=source,operation=operation,parameters=parameters))))
    return task,observations(task)


def range_result(task,facts):
    return next(row for row in analyze(task,facts)['results'] if row['method']=='raw_range_v1')


class ExactRangeSelectionTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'))
        guard.start();self.addCleanup(guard.stop)

    def test_exact_originals_select_both_extrema_for_each_nav_source(self):
        for field in ('unit_nav','ths_unit_nvnf_fund'):
            with self.subTest(field=field):
                task,facts=scenario(field=field)
                self.assertEqual({row['value'] for row in facts},{1.0})
                before=canonical(dict(task=task,facts=facts))
                result=range_result(task,facts)
                self.assertEqual((result['minimum'],result['maximum']),(VALUES[1],VALUES[2]))
                self.assertEqual(result['data_refs'],['D2','D3'])
                self.assertFalse(result['financial_approval'])
                self.assertEqual(canonical(dict(task=task,facts=facts)),before)

    def test_every_observation_order_keeps_raw_extrema_and_their_own_refs(self):
        for values in permutations(VALUES):
            task,facts=scenario(list(values))
            with self.subTest(values=values):
                result=range_result(task,facts)
                self.assertEqual((result['minimum'],result['maximum']),(VALUES[1],VALUES[2]))
                self.assertEqual(result['data_refs'],['D'+str(values.index(VALUES[1])+1),'D'+str(values.index(VALUES[2])+1)])

    def test_missing_middle_does_not_replace_raw_values_or_endpoints(self):
        task,facts=scenario([VALUES[2],None,VALUES[1]])
        result=range_result(task,facts)
        self.assertEqual((result['minimum'],result['maximum']),(VALUES[1],VALUES[2]))
        self.assertEqual(result['data_refs'],['D3','D1'])
        self.assertIsNone(facts[1]['raw_value'])

    def test_missing_endpoint_keeps_the_range_of_available_originals(self):
        task,facts=scenario([VALUES[0],VALUES[1],None])
        result=range_result(task,facts)
        self.assertEqual((result['minimum'],result['maximum']),(VALUES[1],VALUES[0]))
        self.assertFalse(any(row['method']=='raw_change_v1' for row in analyze(task,facts)['results']))

    def test_equal_values_keep_the_first_original_literal_and_one_reference(self):
        task,facts=scenario(['4.0000','4.0','4'])
        result=range_result(task,facts)
        self.assertEqual((result['minimum'],result['maximum']),('4.0000','4.0000'))
        self.assertEqual(result['data_refs'],['D1'])

    def test_regular_values_and_mixed_numeric_tokens_remain_unchanged(self):
        for values,expected in [(['4.0000','3.8000','4.4000'],('3.8000','4.4000')),
                                ([4,3.8,'4.4000'],('3.8','4.4000'))]:
            task,facts=scenario(values)
            result=range_result(task,facts)
            self.assertEqual((result['minimum'],result['maximum']),expected)

    def test_large_finite_values_do_not_collapse_to_the_first_observation(self):
        high='100000000000000002';low='100000000000000001'
        task,facts=scenario([high,low,high])
        self.assertEqual(facts[0]['value'],facts[1]['value'])
        result=range_result(task,facts)
        self.assertEqual((Decimal(result['minimum']),Decimal(result['maximum'])),(Decimal(low),Decimal(high)))
        self.assertEqual(result['data_refs'],['D2','D1'])

    def test_wrong_cached_range_is_excluded_without_dropping_other_calculations(self):
        task,facts=scenario();task['draft_analysis']=analyze(task,facts)
        bad=next(row for row in task['draft_analysis']['results'] if row['method']=='raw_range_v1')
        first=facts[0]
        bad.update(minimum=first['raw_value'],maximum=first['raw_value'],data_refs=[first['id']],
            inputs=dict(a=first['raw_value'],b=first['raw_value']),display=first['raw_value']+'至'+first['raw_value'])
        before=canonical(task)
        result=current_analysis(task,facts)
        self.assertFalse(any(row['method']=='raw_range_v1' for row in result['results']))
        self.assertTrue(any(row['method']=='raw_change_v1' for row in result['results']))
        self.assertEqual(canonical(task),before)

    def test_sqlite_and_both_report_profiles_keep_exact_range_and_reference_pair(self):
        from researchhelper.artifact_exporter import export_report
        from researchhelper.report_designer import design
        task,facts=scenario();task['draft_analysis']=analyze(task,facts)
        bounds=range_result(task,facts)
        paper=normalize_report(dict(title='原值区间观察',conclusion='只描述已取得原值的范围。',
            logics=[dict(title='单位净值范围',analysis='区间由实际取得的原值确定，不代表持有回报或未来预测。',
                calculation_refs=[bounds['id']])]),task,facts)
        paper.update(facts=facts,calculations=task['draft_analysis']['results'],source_binding=source_binding(task))
        task.update(draft_report=paper,status='部分完成',revision=0,gaps=[])
        before=canonical(task)
        with tempfile.TemporaryDirectory(prefix='rh-exact-range-') as folder:
            archive=ResearchArchive(Path(folder)/'store');archive.save(task)
            restored=archive.get(task['id']);self.assertEqual(restored,task)
            content=compose(restored)
            for profile in ('standard','detailed'):
                document=design(content,RESOURCES,profile=profile)['document']
                calculation=next(row for row in document['dataset']['calculations'] if row['method']=='raw_range_v1')
                self.assertEqual((calculation['minimum'],calculation['maximum']),(VALUES[1],VALUES[2]))
                original=next(row for row in content['draft']['calculations'] if row['id']==bounds['id'])
                self.assertEqual(original['data_refs'],['D2','D3'])
                receipt=export_report(content,RESOURCES,Path(folder)/'exports',profile=profile)
                frozen=json.loads(Path(receipt['path']).with_suffix('.json').read_text())
                self.assertEqual(frozen,content)
                self.assertIn(VALUES[1],Path(receipt['path']).read_text())
                self.assertIn(VALUES[2],Path(receipt['path']).read_text())
                workbook_receipt=export_report(content,RESOURCES,Path(folder)/'exports',profile=profile,format='xlsx')
                from openpyxl import load_workbook
                workbook=load_workbook(workbook_receipt['path']);sheet=workbook['计算依据']
                self.assertEqual([cell.value for cell in sheet[1]],
                    ['指标','方法','结果','单位','区间下界','区间上界','对象','基期','报告期或区间','适用条件','原值依据'])
                row=next(row for row in sheet.iter_rows(min_row=2) if row[1].value=='raw_range_v1')
                self.assertEqual((row[4].value,row[5].value),(VALUES[1],VALUES[2]))
                self.assertEqual((row[4].data_type,row[5].data_type),('s','s'))
                inputs=workbook['引用原值']
                start=int(row[-1].hyperlink.location.split('!A')[1])
                self.assertEqual([inputs.cell(index,2).value for index in (start,start+1)],['最小值','最大值'])
                for index,value in zip((start,start+1),(VALUES[1],VALUES[2])):
                    self.assertEqual(inputs.cell(index,1).hyperlink.location,f"'计算依据'!{row[2].coordinate}")
                    cell=inputs.cell(index,9).hyperlink.location.split('!')[1]
                    self.assertEqual(workbook['研究数据'][cell].value,value)
                self.assertTrue(all(cell.data_type!='f' for row in sheet for cell in row))
            restored_before=canonical(restored)
            self.assertEqual(canonical(archive.get(task['id'])),restored_before)
        save_metadata = {'revision', 'updated_at'}
        self.assertEqual(canonical({k:v for k,v in task.items() if k not in save_metadata}),
            canonical({k:v for k,v in json.loads(before).items() if k not in save_metadata}))

    def test_scalar_only_workbook_keeps_its_existing_four_column_contract(self):
        from researchhelper.artifact_exporter import _tabular
        from openpyxl import load_workbook
        raw=_tabular(dict(export_rows=[],export_calculations=[dict(label='读数差',
            method='raw_difference_v1',value=-2,unit='元')]),'xlsx')
        sheet=load_workbook(BytesIO(raw))['计算依据']
        self.assertEqual(list(sheet.values),[('指标','方法','结果','单位'),('读数差','raw_difference_v1',-2,'元')])
