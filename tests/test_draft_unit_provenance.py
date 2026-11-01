"""Units retain their actual authority, conflicts remain local to the affected cells."""
from copy import deepcopy
import socket,tempfile,unittest
from unittest.mock import patch
from lxml import html
from tests import ROOT
from tests.test_macro_argument_visuals import fixture as macro_content
from tests.test_draft_financial_period import sample,add_record,rehash
from researchhelper.contracts import canonical,fingerprint
from researchhelper.draft_sources import observations,writing_context,source_issues,READER_POLICY
from researchhelper.draft_analysis import analyze
from researchhelper.drafts import normalize_report,compose,source_binding,logic_basis
from researchhelper.publication_content import unit_basis_table
from researchhelper.report_designer import design
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.economic_sources import search_response
import json


def macro_task():
    c=macro_content()
    return dict(id=c['task_id'],version=c['research_version'],input_hash=c['input_hash'],cutoff=c['cutoff'],
        source_records=deepcopy(c['source_records']),materials=[],questions=[],evidence=[],calculations=[],knowledge={},
        synthetic=True,entry='宏观',intent='综合研究',question='比较CPI及分项读数',objects=['中国CPI'],research_mode='current',draft_source_reader_version=8)


def conflict(task,unit='指数点'):
    source=deepcopy(next(r for r in task['source_records'] if r['operation']=='search_edb'))
    row=deepcopy(search_response(source['snapshot'])[0]);row['unit']=unit
    source.update(id='conflicting-directory',snapshot=dict(text='```json\n'+json.dumps([row],ensure_ascii=False)+'\n```'))
    source['hash']=fingerprint(source['snapshot']);task['source_records'].append(source);return source


def report(task):
    facts=observations(task);task['draft_analysis']=analyze(task,facts)
    value=dict(title='单位与数据观察',conclusion='使用已取得的实际数值。',logics=[dict(title='已取得读数',analysis='保留明确值，不按目录顺序选择冲突单位。',data_refs=[r['id'] for r in facts])])
    normalized=normalize_report(value,task,facts)
    task['draft_report']=dict(normalized,facts=facts,calculations=task['draft_analysis']['results'],source_binding=source_binding(task))
    return compose(task)


class DraftUnitProvenanceTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'))
        guard.start();self.addCleanup(guard.stop)

    def test_macro_units_point_to_exact_owned_directory_and_snapshot(self):
        task=macro_task();before=canonical(task);facts=observations(task)
        for row in facts:
            self.assertEqual(row['unit'],'%');self.assertEqual(row['unit_basis'],'指标目录口径')
            ref=row['unit_reference'];self.assertEqual(ref['kind'],'source_metadata')
            self.assertEqual(ref['status'],'明确');citation=ref['citations'][0]
            source=next(r for r in task['source_records'] if r['id']==citation['artifact_id'])
            self.assertEqual(citation['snapshot_hash'],fingerprint(source['snapshot']))
            self.assertEqual(citation['locator'],dict(indicator_id=row['code'],field='unit'))
            self.assertEqual(citation['raw_unit'],'%')
        self.assertEqual(canonical(task),before)

    def test_conflicting_units_do_not_depend_on_source_order(self):
        task=macro_task();extra=conflict(task);a=observations(task)
        task['source_records']=[extra]+task['source_records'][:-1];b=observations(task)
        self.assertEqual(a,b)
        affected=[r for r in a if r['code']=='M002826730']
        self.assertEqual({r['unit'] for r in affected},{'原始单位待核对'})
        self.assertTrue(all(r['unit_reference']['status']=='冲突' for r in affected))
        self.assertTrue(all(r['unit']=='%' for r in a if r not in affected))

    def test_conflict_retains_every_value_and_only_changes_affected_arithmetic(self):
        task=macro_task();before=observations(task);conflict(task);after=observations(task)
        self.assertEqual([(r['code'],r['period'],r['raw_value']) for r in before],[(r['code'],r['period'],r['raw_value']) for r in after])
        calculations=analyze(task,after)['results']
        self.assertFalse(any(r['code']=='M002826730' and r['method']=='raw_percentage_points_v1' for r in calculations))
        self.assertEqual(len([r for r in calculations if r['method']=='raw_percentage_points_v1']),4)
        notices=source_issues(task);self.assertTrue(any(r.get('partial') and '单位不一致' in r['reason'] for r in notices))

    def test_invalid_metadata_hash_does_not_label_the_numeric_rows(self):
        task=macro_task();directory=next(r for r in task['source_records'] if r['operation']=='search_edb');directory['hash']='changed'
        rows=observations(task);self.assertEqual(len(rows),12)
        self.assertTrue(all(r['unit']=='原始单位待核对' and 'unit_reference' not in r for r in rows))

    def test_foreign_task_version_and_provider_metadata_cannot_override_units(self):
        for key,value in [('task_id','other'),('research_version',2),('source','tinyshare')]:
            task=macro_task();extra=conflict(task);extra[key]=value
            with self.subTest(key=key):self.assertEqual({r['unit'] for r in observations(task)},{'%'})

    def test_version_seven_retains_its_frozen_reading(self):
        task=macro_task();task[READER_POLICY]=7;extra=conflict(task)
        rows=observations(task);self.assertEqual({r['unit'] for r in rows if r['code']=='M002826730'},{'指数点'})
        self.assertFalse(any('unit_reference' in r for r in rows));self.assertEqual(unit_basis_table(rows,task['source_records']),None)

    def test_unknown_annual_financial_units_are_not_filled_from_another_period(self):
        task=sample();task[READER_POLICY]=8;facts=observations(task)
        self.assertEqual({r['unit'] for r in facts},{'原始单位待核对'})
        self.assertTrue(all('unit_reference' not in r for r in facts))
        self.assertEqual(len([r for r in analyze(task,facts)['results'] if r['method']=='raw_change_v1']),3)

    def test_exact_financial_calibration_is_distinguished_from_literal_api_unit(self):
        task=sample();task[READER_POLICY]=8;task['source_records']=[];add_record(task,'20260630',(120,12,18))
        rows=observations(task);self.assertEqual({r['unit'] for r in rows},{'元'})
        self.assertTrue(all(r['unit_basis']=='限定范围的原件校准' for r in rows))
        for r in rows:
            ref=r['unit_reference'];self.assertEqual(ref['kind'],'calibrated_definition')
            self.assertEqual(ref['scope'],dict(codes=['600519.SH'],periods=['20260630'],report_type='1'))
            self.assertIn('PDF',ref['locator']);self.assertTrue(ref['basis_hash'])

    def test_financial_calibration_does_not_expand_code_statement_or_operation_scope(self):
        for mode in ('code','statement','operation'):
            task=sample();task[READER_POLICY]=8;task['source_records']=[];r=add_record(task,'20260630',(120,12,18))
            if mode=='code':r['parameters']['codes']='000001.SZ';r['snapshot']['tables'][0]['thscode']='000001.SZ'
            elif mode=='statement':
                for item in r['parameters']['indipara']:item['indiparams'][1]='2'
            else:r['operation']='date_sequence'
            rehash(r)
            with self.subTest(mode=mode):self.assertEqual({row['unit'] for row in observations(task)},{'原始单位待核对'})

    def test_calibration_review_time_does_not_move_before_task_cutoff(self):
        task=sample();task[READER_POLICY]=8;task['cutoff']='2026-10-22T00:00:00+08:00';task['source_records']=[]
        add_record(task,'20260630',(120,12,18));rows=observations(task)
        self.assertEqual(len(rows),3);self.assertEqual({r['unit'] for r in rows},{'原始单位待核对'})

    def test_quotation_unit_names_the_registered_field_definition(self):
        from tests.test_observed_window_visuals import fixture
        c=fixture();task=macro_task();task['source_records']=deepcopy(c['source_records']);task['id']=c['task_id'];task['version']=c['research_version'];task['input_hash']=c['input_hash']
        rows=[r for r in observations(task) if r['field']=='close'];self.assertEqual(len(rows),5)
        self.assertTrue(all(r['unit']=='元' and r['unit_reference']['kind']=='registered_field' for r in rows))
        self.assertTrue(all(r['unit_basis']=='已登记字段口径' for r in rows))

    def test_writer_gets_a_detached_reference_without_repeating_it_per_cell(self):
        task=macro_task();facts=observations(task);context=writing_context(task,facts)
        self.assertEqual(len(context['observations']),3)
        projected=context['observations'][0]['unit_reference']
        self.assertEqual(projected['kind'],'source_metadata')
        self.assertEqual(projected['source_refs'],[facts[0]['unit_reference']['citations'][0]['artifact_id']])
        self.assertNotIn('snapshot_hash',canonical(projected))
        projected['source_refs'][0]='changed'
        self.assertEqual(facts[0]['unit_reference']['citations'][0]['artifact_id'],'macro-directory')

    def test_repeated_unit_metadata_does_not_expand_the_model_prompt(self):
        task=macro_task();directory=next(r for r in task['source_records'] if r['operation']=='search_edb')
        for index in range(20):
            copy=deepcopy(directory);copy['id']='duplicate-'+str(index);task['source_records'].append(copy)
        facts=observations(task);self.assertEqual(len(facts[0]['unit_reference']['citations']),21)
        projected=writing_context(task,facts)['observations'][0]['unit_reference']
        self.assertEqual(len(projected['source_refs']),1);self.assertEqual(projected['omitted_citations'],20)
        self.assertLess(len(canonical(projected)),160)

    def test_logic_basis_includes_the_owned_unit_source_without_an_explicit_source_citation(self):
        task=macro_task();content=report(task);rows,sources=logic_basis(content,content['draft']['logics'][0])
        self.assertEqual({r['id'] for r in sources},{'macro-original','macro-directory'})
        table=unit_basis_table(rows,sources);self.assertEqual(len(table),4)
        self.assertEqual({r[-1] for r in table[1:]},{'iFinD指标目录'})

    def test_tampered_unit_citation_is_not_presented_as_verified_directory(self):
        task=macro_task();rows=observations(task)
        for key,value in [('artifact_id','foreign'),('snapshot_hash','changed')]:
            changed=deepcopy(rows);changed[0]['unit_reference']['citations'][0][key]=value
            with self.subTest(key=key):self.assertEqual(unit_basis_table(changed,task['source_records'])[1][-1],'单位依据待定位')
        changed=deepcopy(rows);changed[0]['unit']='元'
        self.assertEqual(unit_basis_table(changed,task['source_records'])[1][-1],'单位依据待定位')

    def test_unit_explanation_is_in_click_detail_and_internal_report_only(self):
        content=report(macro_task());before=canonical(content)
        standard=html.document_fromstring(design(content,ROOT)['html']);detailed=html.document_fromstring(design(content,ROOT,profile='detailed')['html'])
        self.assertNotIn('单位依据',standard.xpath('//main')[0].text_content())
        self.assertIn('单位依据',standard.text_content());self.assertIn('iFinD指标目录',detailed.xpath('//main')[0].text_content())
        self.assertFalse(standard.xpath('//details|//summary'));self.assertEqual(canonical(content),before)

    def test_conflict_does_not_prevent_the_app_engine_saving_a_report(self):
        with tempfile.TemporaryDirectory() as folder:
            archive=ResearchArchive(folder);engine=ResearchAgent(archive,ROOT,offline=True,require_argument_support=True,draft_first=True)
            try:
                current=engine.create('比较CPI及分项，生成研究草稿',entry='宏观',research_mode='current');wire=macro_task();conflict(wire)
                for source in wire['source_records']:source.update(task_id=current['id'],research_version=1)
                current['source_records']=wire['source_records'];archive.save(current)
                saved=engine.start(current['id'],dict(data_requests=[],draft_report=dict(title='已取得数据',conclusion='保留可用分析。',logics=[dict(title='数据观察',analysis='单位冲突仅限制对应解释。')])))
                self.assertTrue(saved.get('delivery'));self.assertEqual(len(saved['draft_report']['facts']),12)
                self.assertEqual(saved['ledger']['used']['model_calls'],0);self.assertEqual(saved['ledger']['used']['data_requests'],0)
            finally:engine.close_runtime()


if __name__=='__main__':unittest.main()
