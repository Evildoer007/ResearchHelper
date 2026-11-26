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


class DraftQuantityPresentationTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'))
        guard.start();self.addCleanup(guard.stop)

    def row(self, value, **extra):
        return dict(dict(id='D1',code='600519.SH',field='ths_pe_ttm_stock',label='PE(TTM)',
            value=float(value),raw_value=str(value),unit='原始单位待核对'),**extra)

    def test_unconfirmed_valuation_units_are_corrected_in_the_shared_draft_and_exports(self):
        from tests.test_client_report_audience import fixture
        task,_=fixture();task['draft_client_copy_version']=2
        facts=task['draft_report']['facts']
        current=next(row for row in facts if row['id']=='D12')
        current.update(value=21.896008878325,raw_value=21.896008878325)
        task['draft_analysis']=analyze(task,facts)
        proposal=dict(title='贵州茅台估值观察',
            conclusion='市盈率TTM由20.2倍升至21.90倍，收盘价1335元。若PE为21.90倍，重新审视假设。',
            logics=[dict(title='PE(TTM)为21.90倍时的情景',data_refs=['D8','D12'],
                argument=dict(claim='盈利改善是估值支撑的前提。',evidence='P/E(TTM)约21.90x。',
                              reasoning='盈利回落可能加大定价压力。'),
                mechanism=dict(steps=[dict(kind='observation',label='PE(TTM)报21.90倍',data_refs=['D12']),
                                      dict(kind='condition',label='盈利兑现决定估值支撑',data_refs=[])]))],
            risks=['PE读数为21.90%，盈利口径仍需核对。'])
        before=canonical((facts,proposal))
        normalized=normalize_report(proposal,task,facts)
        self.assertEqual(canonical((facts,proposal)),before)
        self.assertEqual(normalized['title'],proposal['title'])
        self.assertEqual(normalized['logics'][0]['title'],proposal['logics'][0]['title'])
        self.assertIn('市盈率TTM由20.2升至21.90（单位未明确），收盘价1335元',normalized['conclusion'])
        self.assertIn('若PE为21.90倍',normalized['conclusion'])
        self.assertIn('P/E(TTM)约21.90（单位未明确）',normalized['logics'][0]['analysis'])
        self.assertEqual(normalized['logics'][0]['mechanism']['steps'][0]['label'],'PE(TTM)报21.90（单位未明确）')
        self.assertIn('PE读数为21.90（单位未明确）',normalized['risks'][0])
        edits=normalized['copy_adjustments']
        self.assertEqual(len(edits),5)
        self.assertTrue(all(row['reason']=='valuation_unit_unconfirmed' and row['financial_approval'] is False for row in edits))
        task['draft_report']=dict(normalized,facts=facts,calculations=task['draft_analysis']['results'],source_binding=source_binding(task))
        content=compose(task)
        for profile in ('standard','detailed'):
            result=design(content,ROOT,profile=profile);doc=result['document']
            visible=html.document_fromstring(result['html']).find('.//main').text_content()
            blocks=' '.join(block.get('text','') for block in doc['blocks'])
            for text in (visible,blocks):
                self.assertIn('P/E(TTM)约21.90（单位未明确）',text)
                self.assertNotIn('P/E(TTM)约21.90x',text)
            self.assertTrue(any(row[3]==current['value'] and row[4]=='原始单位待核对' for row in doc['dataset']['rows']))

    def test_unconfirmed_valuation_copy_preserves_known_ambiguous_and_conditional_numbers(self):
        from researchhelper.publication_content import _valuation_unit_corrections
        unknown=self.row('21.896008878325')
        other=dict(unknown,id='D2',code='000001.SZ')
        for text,rows in (
            ('市盈率约21.90倍。',[dict(unknown,unit='倍')]),
            ('市盈率约21.90倍。',[unknown,other]),
            ('市盈率约21.90倍。',[unknown,dict(unknown,id='D2',unit='倍')]),
            ('市盈率约21.90倍。',[dict(unknown,raw_value='99.0')]),
            ('市盈率约21.91倍。',[unknown]),
            ('PE变化21.90%，假设PE为21.90倍。',[unknown]),
            ('目标PE为21.90倍，市盈率21.90%分位，市盈率21.90倍于同行。',[unknown]),
            ('经营现金流/净利润为21.90倍，PE为21.90。',[unknown]),
        ):
            with self.subTest(text=text,rows=rows):
                self.assertEqual(_valuation_unit_corrections(text,rows),[])
        pb=dict(unknown,field='ths_pb_mrq_stock',value=2.1,raw_value=2.1)
        edits=_valuation_unit_corrections('市净率(MRQ)报2.1倍。',[pb,dict(pb,value=1e100,raw_value=1e100)])
        self.assertEqual([row['replacement'] for row in edits],['2.1（单位未明确）'])

    def display(self,text,rows):
        from researchhelper.publication_content import draft_quantity_presentation
        changes=draft_quantity_presentation(text,rows)
        for item in reversed(changes):text=text[:item['start']]+item['label']+text[item['end']:]
        return text,changes

    def content(self):
        from tests.test_client_report_audience import fixture
        task,_=fixture();facts=task['draft_report']['facts']
        for ident,value in [('D8',20.24745592025),('D12',21.896008878325)]:
            row=next(r for r in facts if r['id']==ident);row.update(value=value,raw_value=value)
        task['draft_analysis']=analyze(task,facts)
        text='PE(TTM)读数由20.24745592025升至21.896008878325，单位仍未明确，不换算。'
        value=dict(title='估值观察',conclusion=text,logics=[dict(title='估值变化仍需盈利验证',analysis=text,
            data_refs=['D8','D12'],risk='盈利口径变化可能影响比较。')],risks=['读数变化不等于估值高低结论。'])
        normalized=normalize_report(value,task,facts)
        task['draft_report']=dict(normalized,facts=facts,calculations=task['draft_analysis']['results'],source_binding=source_binding(task))
        return compose(task)

    def test_exact_named_readings_use_readable_precision_without_approving_units(self):
        rows=[self.row('20.24745592025'),self.row('21.896008878325',id='D2')]
        before=canonical(rows)
        text,changes=self.display('PE(TTM)由20.24745592025升至21.896008878325。',rows)
        self.assertEqual(text,'PE(TTM)由20.25升至21.90。')
        self.assertEqual([v['data_refs'] for v in changes],[['D1'],['D2']])
        self.assertNotIn('倍',text);self.assertEqual(canonical(rows),before)

    def test_small_negative_readings_keep_their_sign_and_do_not_become_zero(self):
        for raw in ['-0.0000047','0.000000041']:
            text,changes=self.display('市盈率读数'+raw+'，仅按原值观察。',[self.row(raw)])
            self.assertTrue(changes)
            self.assertNotEqual(float(changes[0]['label']),0)
            self.assertEqual(changes[0]['label'].startswith('-'),raw.startswith('-'))
        text,changes=self.display('PE为0.0000000000041。',[self.row('0.0000000000041')])
        self.assertEqual(changes,[]);self.assertIn('0.0000000000041',text)

    def test_amounts_dates_codes_and_unmapped_prose_are_not_reinterpreted(self):
        text='代码600519.SH，2026-11-02，收入168,838,102,514.7900，比例20.24745592025%。'
        row=self.row('168838102514.79',field='ths_revenue_stock')
        shown,changes=self.display(text,[row,self.row('20.24745592025',id='D2')])
        self.assertEqual(shown,text);self.assertEqual(changes,[])
        text='待核对读数20.24745592025，不能识别指标。'
        self.assertEqual(self.display(text,[self.row('20.24745592025')]),(text,[]))

    def test_mixed_metrics_objects_and_conflicting_values_are_left_verbatim(self):
        for rows,text in [
            ([self.row('20.24745592025'),self.row('20.24745592025',id='D2',field='ths_pb_mrq_stock')],'PE与PB均为20.24745592025。'),
            ([self.row('20.24745592025'),self.row('20.24745592025',id='D2',code='000001.SZ')],'PE为20.24745592025。'),
            ([self.row('20.24745592025'),self.row('20.24745592025',id='D2',field='ths_revenue_stock')],'PE为20.24745592025。'),
            ([self.row('20.24745592025',raw_value='99.1')],'PE为20.24745592025。')]:
            with self.subTest(text=text,rows=rows):self.assertEqual(self.display(text,rows),(text,[]))

    def test_literal_grouping_and_repeated_periods_keep_original_spans(self):
        rows=[self.row('1020.24745592025'),self.row('1020.24745592025',id='D2')]
        text='市盈率为1,020.24745592025，比较条件不变。'
        shown,changes=self.display(text,rows)
        self.assertIn('1020.25',shown);self.assertEqual(changes[0]['original'],'1,020.24745592025')
        self.assertEqual(changes[0]['data_refs'],['D1','D2'])

    def test_client_internal_and_dataset_keep_one_frozen_result(self):
        from researchhelper.publication_content import publication_dataset
        content=self.content();before=canonical(content);dataset=publication_dataset(content)
        for profile in ('standard','detailed'):
            result=design(content,ROOT,profile=profile);tree=html.document_fromstring(result['html'])
            main=tree.find('.//main');links=main.xpath('.//a[@class="quantity-link"]')
            self.assertTrue(links);self.assertIn('20.25',main.text_content());self.assertIn('21.90',main.text_content())
            if profile=='standard':self.assertNotIn('21.896008878325',main.text_content())
            for link in links:
                target=tree.get_element_by_id(link.get('href')[1:])
                self.assertIn(link.get('data-original-display'),target.text_content())
                self.assertEqual(link.get('aria-haspopup'),'dialog')
            self.assertEqual(result['document']['dataset'],dataset)
            import hashlib
            self.assertEqual(result['document']['content_hash'],hashlib.sha256(canonical(content).encode()).hexdigest())
        self.assertEqual(canonical(content),before)

    def test_foreign_source_does_not_provide_a_display_binding(self):
        content=self.content()
        for source in content['source_records']:
            if source['id']=='market':source['task_id']='another-task'
        content['content_hash']=fingerprint({k:v for k,v in content.items() if k!='content_hash'})
        tree=html.document_fromstring(design(content,ROOT)['html']);main=tree.find('.//main')
        self.assertFalse(main.xpath('.//a[@class="quantity-link"]'))
        self.assertIn('21.896008878325',main.text_content())

    def test_safe_html_keeps_non_numeric_words_and_escapes_untrusted_markup(self):
        content=self.content();logic=content['draft']['logics'][0]
        logic['analysis']='<img src=x onerror=bad()>PE(TTM)为21.896008878325，保留竞争解释。'
        content['content_hash']=fingerprint({k:v for k,v in content.items() if k!='content_hash'})
        tree=html.document_fromstring(design(content,ROOT)['html']);main=tree.find('.//main')
        self.assertFalse(main.xpath('.//img'));self.assertIn('<img src=x onerror=bad()>',main.text_content())
        self.assertIn('21.90，保留竞争解释。',main.text_content())




    def test_client_table_rounds_only_valuation_and_preserves_full_linked_evidence(self):
        from researchhelper.publication_content import publication_dataset
        content=self.content()
        content['draft']['logics'][0]['data_refs']=['D12']
        content['draft']['logics'][0]['calculation_refs']=[]
        content['content_hash']=fingerprint({key:value for key,value in content.items() if key!='content_hash'})
        before=canonical(content);dataset=publication_dataset(content)
        result=design(content,ROOT);tree=html.document_fromstring(result['html'])
        row=tree.xpath('//main//tr[@data-observation-id="D12"]')[0]
        self.assertIn('21.90',row.text_content())
        self.assertNotIn('21.896008878325',row.text_content())
        self.assertIn('未确认',row.text_content());self.assertNotIn('倍',row.text_content())
        target=tree.get_element_by_id(row.xpath('.//a/@href')[0][1:])
        self.assertIn('21.896008878325',target.text_content())
        self.assertIn('原始单位待核对',target.text_content())
        self.assertEqual(result['document']['dataset'],dataset)
        detailed=design(content,ROOT,profile='detailed');detail_tree=html.document_fromstring(detailed['html'])
        self.assertIn('21.896008878325',detail_tree.find('.//main').text_content())
        self.assertEqual(canonical(content),before)

    def test_legacy_report_does_not_adopt_the_new_table_or_date_labels(self):
        from researchhelper.draft_visuals import logic_figure_plan
        content=self.content();content['draft'].pop('reader_format_version')
        content['draft']['logics'][0]['data_refs']=['D12']
        content['draft']['logics'][0]['calculation_refs']=[]
        content['content_hash']=fingerprint({key:value for key,value in content.items() if key!='content_hash'})
        result=design(content,ROOT);tree=html.document_fromstring(result['html'])
        row=tree.xpath('//main//tr[@data-observation-id="D12"]')[0]
        self.assertIn('21.896008878325',row.text_content());self.assertIn('原始单位待核对',row.text_content())
        self.assertTrue(all('dateLabels' not in spec.get('auto_visual',{}).get('data',{}) for spec in logic_figure_plan(content)))

    def test_calendar_labels_do_not_guess_invalid_dates_quarters_or_identifiers(self):
        from researchhelper.publication_content import reader_date_label,observation_presentation
        self.assertEqual(reader_date_label('20241231'),'2024-12-31')
        self.assertEqual(reader_date_label('20240229'),'2024-02-29')
        for value in ('20250229','20261301','2026Q3','600519.SH','2024-12-31',None,20241231):
            self.assertEqual(reader_date_label(value),value)
        row=self.row('168838102514.79',field='ths_revenue_stock',period='20241231')
        before=canonical(row);shown=observation_presentation(row)
        self.assertIn('168',shown['value']);self.assertNotIn('亿',shown['value'])
        self.assertEqual(shown['period'],'2024-12-31');self.assertEqual(canonical(row),before)

    def test_new_chart_date_labels_are_separate_from_actual_values_and_periods(self):
        from researchhelper.draft_visuals import logic_figure_plan
        from tests.test_client_report_audience import fixture
        task,content=fixture();facts=content['draft']['facts']
        for period,value in [('20241231',5000000000),('20251231',4500000000)]:
            row=dict(id='reported-net-'+period,artifact_id='financial',code='600519.SH',field='ths_np_stock',label='净利润',
                period=period,value=value,raw_value=value,unit='原始单位待核对',series_basis='annual',axis_role='time')
            facts.append(row);content['draft']['logics'][1]['data_refs'].append(row['id'])
        content['draft']['calculations']=analyze(task,facts)['results'];before=canonical(content)
        charts=logic_figure_plan(content);dates=[spec for spec in charts if spec.get('auto_visual',{}).get('data',{}).get('dateLabels')]
        self.assertTrue(dates)
        for spec in dates:
            data=spec['auto_visual']['data']
            self.assertEqual(data['dateLabels']['20241231'],'2024-12-31')
            self.assertIn('20241231',data.get('periods',data.get('comparison_periods',[])))
        self.assertEqual(canonical(content),before)


class AnnualFinancialUnitTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'))
        guard.start();self.addCleanup(guard.stop)

    def task(self):
        task=sample();task[READER_POLICY]=9;task['cutoff']='2026-11-03T23:59:59+08:00'
        return task

    def test_reviewed_annual_scope_labels_new_reader_without_rewriting_raw_cells(self):
        task=self.task();before=canonical(task);rows=observations(task)
        self.assertEqual(len(rows),6)
        self.assertEqual({r['unit'] for r in rows},{'元'})
        self.assertEqual(canonical(task),before)
        for row in rows:
            self.assertEqual(row['unit_reference']['scope'],dict(codes=['600519.SH'],periods=['20241231','20251231'],report_type='1'))
            self.assertEqual(row['unit_basis'],'限定范围的原件校准')
        self.assertTrue(all('限定范围' in row[-1] for row in unit_basis_table(rows,task['source_records'])[1:]))
        self.assertFalse(analyze(task,rows)['financial_approval'])

    def test_reader_eight_keeps_its_original_annual_unknown_units(self):
        task=self.task();task[READER_POLICY]=8
        self.assertEqual({r['unit'] for r in observations(task)},{'原始单位待核对'})

    def test_review_time_does_not_backdate_annual_authority(self):
        task=self.task();task['cutoff']='2026-11-03T15:00:00+00:00'
        rows=observations(task)
        self.assertEqual(len(rows),6);self.assertTrue(all(r['unit']=='原始单位待核对' for r in rows))

    def test_annual_definition_cannot_cross_security_statement_or_period(self):
        for mode in ('security','statement','period','operation'):
            task=self.task()
            for record in task['source_records']:
                if mode=='security':record['parameters']['codes']='000001.SZ';record['snapshot']['tables'][0]['thscode']='000001.SZ'
                elif mode=='statement':
                    for item in record['parameters']['indipara']:item['indiparams'][1]='2'
                elif mode=='period':
                    for item in record['parameters']['indipara']:item['indiparams'][0]='20231231'
                elif mode=='operation':record['operation']='date_sequence'
                rehash(record)
            with self.subTest(mode=mode):
                rows=observations(task)
                self.assertEqual(len(rows),6)
                self.assertTrue(all(r['unit']=='原始单位待核对' for r in rows))

    def test_old_metadata_digest_and_references_stay_identical(self):
        from researchhelper.ifind_financial_catalog import catalogue_digest,evidence_definitions
        self.assertEqual(catalogue_digest(),'e909888ea6209fcd86e38573b795be644eb6358cfd6e34f02bf795201a4812bf')
        old=evidence_definitions();new=evidence_definitions(2)
        self.assertEqual(len(old),4);self.assertEqual(len(new),12)
        for key,value in old.items():self.assertEqual(new[key],value)
        new['ths_revenue_stock@annual-2025']['claims']['unit']='万元'
        self.assertEqual(evidence_definitions(2)['ths_revenue_stock@annual-2025']['claims']['unit'],'元')

    def test_inspector_rejects_changed_calibration_references_without_blocking_values(self):
        task=self.task();facts=observations(task)
        for key in ('definition_hash','basis_hash','source','locator','available_at','scope'):
            rows=deepcopy(facts);rows[0]['unit_reference'][key]='changed'
            with self.subTest(key=key):
                table=unit_basis_table(rows,task['source_records'])
                self.assertEqual(table[1][-1],'单位依据待定位')
                self.assertEqual([r['raw_value'] for r in rows],[r['raw_value'] for r in facts])
        for key,value in [('unit','万元'),('code','000001.SZ'),('period','20231231'),('field','ths_np_stock')]:
            rows=deepcopy(facts);rows[0][key]=value
            with self.subTest(key=key):self.assertEqual(unit_basis_table(rows,task['source_records'])[1][-1],'单位依据待定位')

    def test_inspector_rechecks_actual_request_and_source_cell(self):
        task=self.task();facts=observations(task)
        for mode in ('statement','code','value','hash'):
            sources=deepcopy(task['source_records'])
            if mode=='statement':sources[0]['parameters']['indipara'][0]['indiparams'][1]='2'
            elif mode=='code':sources[0]['parameters']['codes']='000001.SZ'
            elif mode=='value':sources[0]['snapshot']['tables'][0]['table'][facts[0]['field']][0]=999
            else:sources[0]['hash']='changed'
            with self.subTest(mode=mode):self.assertEqual(unit_basis_table(facts,sources)[1][-1],'单位依据待定位')


if __name__=='__main__':unittest.main()
