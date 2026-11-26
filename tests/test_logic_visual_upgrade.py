"""Independent ten-round report visualization review; no paid dispatch."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import socket
import unittest
from unittest.mock import patch
from lxml import html
from tests import ROOT
from tests.test_client_report_audience import fixture
from researchhelper.contracts import canonical,fingerprint
from researchhelper.draft_visuals import logic_figure_plan
from researchhelper.report_designer import design


def rehash(content):
    content['content_hash']=fingerprint({key:value for key,value in content.items() if key!='content_hash'})
    return content


class LogicVisualUpgradeTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('Offline only'))
        guard.start();self.addCleanup(guard.stop)
        _,self.content=fixture()

    def referenced_path(self):
        content=deepcopy(self.content)
        content['draft']['logics'][0].update(title='传导渠道',analysis='传导关系需要分别核对。',data_refs=[],calculation_refs=[],
            mechanism=dict(steps=[dict(label='观察条件',kind='observation',data_refs=['D1']),
                                  dict(label='传导环节',kind='condition',data_refs=['D4']),
                                  dict(label='可能影响',kind='hypothesis',data_refs=[])]))
        return rehash(content)

    def test_step_references_are_part_of_the_logic_basis_without_mutating_frozen_content(self):
        from researchhelper.drafts import logic_basis
        content=self.referenced_path();before=canonical(content)
        rows,sources=logic_basis(content,content['draft']['logics'][0])
        self.assertEqual({row['id'] for row in rows},{'D1','D4'})
        self.assertEqual({source['id'] for source in sources},{row['artifact_id'] for row in rows})
        self.assertEqual(canonical(content),before)

    def test_new_report_inherits_step_refs_and_reports_bad_refs_once(self):
        from researchhelper.drafts import normalize_report
        task,_=fixture();content=self.referenced_path();value=dict(logics=[deepcopy(content['draft']['logics'][0])])
        logic=value['logics'][0];logic['data_refs']=['D1','foreign']
        logic['mechanism']['steps'][0]['data_refs']=['D1','foreign']
        logic['mechanism']['steps'][1]['data_refs']=['D4','foreign']
        before=canonical(value);result=normalize_report(value,task,content['draft']['facts'])['logics'][0]
        self.assertEqual(result['data_refs'],['D1','D4'])
        self.assertEqual(len(result['reference_notes']),len(set(result['reference_notes'])))
        self.assertNotIn('foreign',str(result['mechanism']))
        self.assertEqual(canonical(value),before)

    def test_explicit_path_and_sparse_readings_survive_reference_resolution(self):
        content=self.referenced_path();before=canonical(content)
        specs=[spec for spec in logic_figure_plan(content) if spec.get('paper_logic')=='logic-1']
        self.assertIn('evidence',[spec['auto_visual']['scheme'] for spec in specs])
        self.assertFalse(any(spec.get('coverage_kind')=='numeric' for spec in specs))
        self.assertTrue(all(set(spec.get('data_refs',[]))<=set(spec['argument_data_refs']) for spec in specs))
        tree=html.document_fromstring(design(content,ROOT)['html'])
        self.assertEqual(set(tree.get_element_by_id('logic-1').xpath('.//p[@class="measured-reading"]/span/@data-observation-id')),{'D1','D4'})
        self.assertEqual(set(tree.get_element_by_id('draft-basis-1').xpath('.//tr/@data-observation-id')),{'D1','D4'})
        self.assertEqual(canonical(content),before)

    def test_detailed_step_basis_respects_source_ownership(self):
        from researchhelper.publication_content import project_content
        content=self.referenced_path()
        blocks=project_content(content,'detailed')['logic_blocks']['logic-1']
        tables=[block for block in blocks if block['kind']=='table']
        self.assertTrue(tables)
        self.assertEqual(len(tables[0]['rows']),3)
        for record in content['source_records']:record['research_version']+=1
        blocks=project_content(rehash(content),'detailed')['logic_blocks']['logic-1']
        self.assertFalse(any(block['kind']=='table' for block in blocks))

    def test_market_pair_stays_together_when_a_path_is_also_explicit(self):
        content=deepcopy(self.content)
        content['draft']['logics'][2]['mechanism']=dict(steps=[dict(label='已有观察'),dict(label='适用条件')])
        content=rehash(content);before=canonical(content)
        for profile in ('standard','detailed'):
            tree=html.document_fromstring(design(content,ROOT,profile=profile)['html'])
            groups=tree.xpath('//section[@id="logic-3"]//div[contains(@class,"paper-figure-group")]')
            self.assertEqual([group.get('data-columns') for group in groups],['2','1'])
        self.assertEqual(canonical(content),before)

    def test_every_data_backed_logic_keeps_a_figure_or_readable_evidence(self):
        tree=html.document_fromstring(design(self.content,ROOT)['html'])
        for index in range(1,4):
            section=tree.get_element_by_id('logic-'+str(index))
            self.assertTrue(section.xpath('.//figure | .//table | .//p[@class="measured-reading"]//span[@data-observation-id or @data-calculation-ref]'))

    def test_no_argument_can_borrow_unreferenced_observations(self):
        for spec in logic_figure_plan(self.content):
            if spec.get('paper_logic'):
                self.assertTrue(set(spec.get('data_refs',[]))<=set(spec['argument_data_refs']))

    def test_two_growth_rates_keep_the_registered_comparisons_without_indexing(self):
        result = design(self.content, ROOT)
        paper = html.document_fromstring(result['html'])
        section = paper.get_element_by_id('logic-1')
        self.assertFalse(section.xpath('.//figure | .//table'))
        readings = section.xpath('.//span[@data-calculation-ref]')
        self.assertEqual([(row.text_content(), row.get('data-calculation-inputs').split()) for row in readings],
                         [('-1.00%', ['D1', 'D4']), ('-10.00%', ['D2', 'D5'])])

    def test_cash_logic_keeps_its_two_raw_readings_and_registered_change(self):
        tree=html.document_fromstring(design(self.content,ROOT)['html'])
        section=tree.get_element_by_id('logic-2')
        self.assertFalse(section.xpath('.//figure'))
        self.assertFalse(section.xpath('.//*[@data-observation-id]'))
        result, = section.xpath('.//p[@class="measured-reading"]/span[@data-calculation-ref]')
        self.assertEqual(result.text_content(), '-33.33%')
        self.assertEqual(result.get('data-calculation-inputs').split(), ['D3', 'D6'])
        source = tree.get_element_by_id('draft-basis-2')
        self.assertEqual(source.xpath('.//tr/@data-observation-id'), ['D3', 'D6'])

    def test_price_and_valuation_share_only_a_verified_date_window(self):
        specs=[s for s in logic_figure_plan(self.content) if s.get('paper_logic')=='logic-3']
        self.assertEqual(len(specs),2)
        self.assertEqual({s['sync_group'] for s in specs},{'logic-3-market'})
        self.assertTrue(all(s['axis_alignment']=='same_observed_dates' for s in specs))
        self.assertTrue(all(s['context']=='2026-10-26至2026-10-28' for s in specs))
        self.assertTrue(all(s['auto_visual']['data']['numberDigits']==2 for s in specs))
        self.assertEqual(specs[0]['dates'],['2026-10-26','2026-10-27','2026-10-28'])

    def test_missing_optional_refs_use_literal_fields_without_blocking_report(self):
        content=deepcopy(self.content)
        for logic in content['draft']['logics']:logic.update(data_refs=[],calculation_refs=[],source_refs=[])
        specs=logic_figure_plan(rehash(content))
        self.assertEqual({spec.get('paper_logic') for spec in specs}, {'logic-3'})
        self.assertEqual({spec['reference_origin'] for spec in specs}, {'literal_topic_match'})
        self.assertEqual({ref for spec in specs for ref in spec['data_refs']}, {'D7', 'D8', 'D9', 'D10', 'D11', 'D12'})

    def test_partial_dates_do_not_get_filled_or_falsely_linked(self):
        content=deepcopy(self.content);content['draft']['logics'][2]['data_refs'].remove('D7')
        specs=[s for s in logic_figure_plan(rehash(content)) if s.get('paper_logic')=='logic-3']
        market,=specs
        self.assertNotIn('sync_group',market)
        self.assertNotEqual(market['field'],'close')
        tree=html.document_fromstring(design(content,ROOT)['html'])
        expected=[row['id'] for row in content['draft']['facts'] if row['field']=='close'
                  and row['id'] in content['draft']['logics'][2]['data_refs']]
        self.assertEqual(tree.get_element_by_id('logic-3').xpath('.//p[@class="measured-reading"]/span/@data-observation-id'),expected)

    def test_foreign_source_cannot_survive_as_a_supplementary_chart(self):
        content=deepcopy(self.content)
        for source in content['source_records']:source['task_id']='foreign'
        self.assertEqual(logic_figure_plan(rehash(content)),[])

    def test_every_plotted_point_resolves_only_its_actual_source_rows(self):
        for spec in logic_figure_plan(self.content):
            if not spec.get('paper_logic'):continue
            self.assertTrue(spec['inspection_target'].startswith('draft-figure-basis-'))
            self.assertTrue(all(set(refs)<=set(spec['data_refs']) for refs in spec['point_refs']))
        tree=html.document_fromstring(design(self.content,ROOT)['html'])
        self.assertTrue(tree.xpath('//main//*[@class="chart"][@tabindex="0"]'))
        for spec in logic_figure_plan(self.content):
            if spec.get('paper_logic'):
                rows=tree.xpath('//*[@id="'+spec['inspection_target']+'"]//tr/@data-observation-id')
                self.assertEqual(set(rows),set(spec['data_refs']))

    def test_qualitative_path_uses_explicit_frozen_steps_without_fake_values(self):
        content=deepcopy(self.content);logic=content['draft']['logics'][0]
        logic.update(title='传导条件',analysis='不同解释需要区分。',data_refs=[],calculation_refs=[],mechanism=dict(steps=[
            dict(label='需求变化',kind='hypothesis'),dict(label='盈利传导',kind='hypothesis'),dict(label='价格兑现条件',kind='condition')]))
        specs=[s for s in logic_figure_plan(rehash(content)) if s.get('paper_logic')=='logic-1']
        self.assertEqual(len(specs),1);spec=specs[0]
        self.assertEqual(spec['coverage_kind'],'mechanism');self.assertIn('非数值预测',spec['context'])
        data=spec['auto_visual']['data'];self.assertEqual(len(data['links']),2)
        self.assertTrue(all('value' not in n and 'probability' not in n for n in data['nodes']))
        self.assertEqual(data['nodes'][0]['name'],'需求变化')
        self.assertTrue(html.document_fromstring(design(content,ROOT)['html']).xpath('//section[@id="logic-1"]//figure'))

    def test_long_mechanism_labels_reserve_print_height_without_changing_content(self):
        content=deepcopy(self.content)
        labels=['需求变化需要结合实际范围及适用条件分别核对' for _ in range(5)]
        content['draft']['logics'][0].update(title='传导条件',analysis='区分不同解释。',data_refs=[],calculation_refs=[],
            mechanism=dict(steps=[dict(label=label,kind='hypothesis') for label in labels]))
        content=rehash(content);before=canonical(content)
        spec=next(spec for spec in logic_figure_plan(content) if spec.get('coverage_kind')=='mechanism')
        self.assertGreater(spec['min_height'],220)
        for profile in ('standard','detailed'):
            tree=html.document_fromstring(design(content,ROOT,profile=profile)['html'])
            groups=tree.xpath('//section[@id="logic-1"]//div[contains(@class,"paper-figure-group")]')
            self.assertEqual(len(groups),1)
            self.assertIn('--paper-print-height:'+str(spec['min_height'])+'px',groups[0].get('style'))
            self.assertIn('--paper-mobile-height:'+str(spec['min_height'])+'px',groups[0].get('style'))
        self.assertEqual(canonical(content),before)

    def test_static_mechanism_keeps_complete_names_links_and_grows_for_narrow_word_columns(self):
        from io import BytesIO
        from pypdf import PdfReader
        from researchhelper.publication_graphics import chart_pdf, mechanism_layout
        labels=['需求变化需要结合实际范围及适用条件分别核对','传导条件需要核对范围与时点',
                '业务影响需要区分总量与个体','竞争解释需要保留相反证据','后续反馈需要重新观察']
        data=dict(qualitative=True,nodes=[dict(id=str(i),name=label,kind='hypothesis') for i,label in enumerate(labels)],
                  links=[dict(source=str(i),target=str(i+1)) for i in range(4)])
        before=canonical(data);layout=mechanism_layout(data,250,90)
        self.assertGreater(layout['height'],90)
        self.assertEqual([''.join(lines) for lines in layout['labels']],labels)
        spec=dict(auto_visual=dict(scheme='evidence',data=data))
        pages=PdfReader(BytesIO(chart_pdf(spec,width=250,height=90))).pages
        self.assertEqual(len(pages),1)
        self.assertAlmostEqual(float(pages[0].mediabox.height),layout['height'],places=3)
        visible=''.join(pages[0].extract_text().split())
        self.assertTrue(all(label in visible for label in labels))
        self.assertEqual(canonical(data),before)

    def long_path(self,count):
        content=deepcopy(self.content)
        names=['需求变化','订单调整','产能反应','交付变化','库存变化','定价调整','成本传导',
               '利润变化','现金回收','融资反馈','投资调整','供给反应','后续验证']
        labels=[names[i%len(names)] for i in range(count)]
        content['draft']['logics'][0].update(title='传导条件',analysis='逐项区分传导条件与相反解释。',
            data_refs=[],calculation_refs=[],mechanism=dict(steps=[dict(label=name,kind='hypothesis') for name in labels]))
        return rehash(content),labels

    def test_long_path_panels_preserve_every_step_and_edge_in_original_order(self):
        from math import ceil
        for count in (2,5,6,9,10,13,31):
            with self.subTest(count=count):
                content,labels=self.long_path(count);before=canonical(content)
                specs=[s for s in logic_figure_plan(content) if s.get('paper_logic')=='logic-1']
                self.assertEqual(len(specs),ceil((count-1)/4))
                expected_edges=[('step-'+str(i),'step-'+str(i+1)) for i in range(count-1)]
                actual_edges=[];visible={};prior=None
                for spec in specs:
                    data=spec['auto_visual']['data']
                    self.assertTrue(2<=len(data['nodes'])<=5)
                    self.assertTrue(all('value' not in node and 'probability' not in node for node in data['nodes']))
                    if prior:self.assertEqual(prior,data['nodes'][0]['id'])
                    prior=data['nodes'][-1]['id']
                    visible.update({node['id']:node['name'] for node in data['nodes']})
                    actual_edges.extend((edge['source'],edge['target']) for edge in data['links'])
                self.assertEqual(actual_edges,expected_edges)
                self.assertEqual([visible['step-'+str(i)] for i in range(count)],labels)
                self.assertEqual(canonical(content),before)

    def test_long_path_is_not_squeezed_into_three_a4_columns(self):
        content,labels=self.long_path(13);before=canonical(content)
        for profile in ('standard','detailed'):
            result=design(content,ROOT,profile=profile)
            tree=html.document_fromstring(result['html'])
            groups=tree.xpath('//section[@id="logic-1"]//div[contains(@class,"paper-figure-group")]')
            self.assertEqual([g.get('data-columns') for g in groups],['1','2'])
            blocks=[b for b in result['document']['blocks'] if b['kind']=='chart' and b['spec'].get('paper_logic')=='logic-1']
            self.assertEqual(len(blocks),3)
            nodes={node['id']:node['name'] for block in blocks for node in block['spec']['auto_visual']['data']['nodes']}
            self.assertEqual([nodes['step-'+str(i)] for i in range(13)],labels)
        self.assertEqual(canonical(content),before)

    def test_static_exports_of_all_path_panels_keep_the_last_step(self):
        from io import BytesIO
        from pypdf import PdfReader
        from researchhelper.publication_graphics import chart_pdf
        content,labels=self.long_path(13)
        specs=[s for s in logic_figure_plan(content) if s.get('paper_logic')=='logic-1']
        texts=[]
        for spec in specs:
            pages=PdfReader(BytesIO(chart_pdf(spec,width=250,height=154))).pages
            self.assertEqual(len(pages),1)
            texts.append(''.join(pages[0].extract_text().split()))
        self.assertTrue(all(label in ''.join(texts) for label in labels))
        self.assertIn(labels[-1],texts[-1])

    def test_sparse_arguments_stay_in_prose_and_matching_series_share_a_row(self):
        for profile in ('standard', 'detailed'):
            paper = html.document_fromstring(design(self.content, ROOT, profile=profile)['html'])
            for number in (1, 2):
                argument = paper.get_element_by_id(f'logic-{number}')
                self.assertFalse(argument.xpath('.//figure | .//div[@class="logic-reading-grid"]'))
            series = paper.get_element_by_id('logic-3')
            group, = series.xpath('.//div[@data-columns="2"]')
            self.assertEqual(len(group.xpath('./figure')), 2)

    def test_frozen_presentation_records_argument_visual_coverage(self):
        document=design(self.content,ROOT)['document']
        coverage = document['figure_coverage']
        self.assertEqual({key: row['figures'] for key, row in coverage.items()},
                         {'logic-1': 0, 'logic-2': 0, 'logic-3': 2})
        self.assertEqual(coverage['logic-1']['status'], 'numeric_text')
        self.assertEqual(coverage['logic-2']['status'], 'numeric_text')
        self.assertEqual(set(coverage['logic-1']['data_refs']), {'D1', 'D2', 'D4', 'D5'})
        self.assertEqual(set(coverage['logic-2']['data_refs']), {'D3', 'D6'})
        self.assertEqual(coverage['logic-3']['schemes'], ['line'])

    def test_static_qualitative_export_keeps_labels_instead_of_an_empty_chart(self):
        from researchhelper.publication_graphics import chart_pdf
        from pypdf import PdfReader
        from io import BytesIO
        spec=dict(auto_visual=dict(scheme='evidence',data=dict(qualitative=True,nodes=[dict(id='a',name='需求变化'),dict(id='b',name='盈利传导')],links=[dict(source='a',target='b')])))
        text=''.join(page.extract_text() for page in PdfReader(BytesIO(chart_pdf(spec))).pages)
        self.assertIn('需求变化',text);self.assertIn('盈利传导',text);self.assertNotIn('原值见下表',text)

    def test_same_writing_call_exposes_optional_mechanism_and_shared_visual_rules(self):
        from researchhelper.draft_workflow import _write
        from researchhelper.publication_content import CLIENT_REPORT_BRIEF
        calls=[]
        class Model:
            def reason(self,task,messages,**options):calls.append((messages,options));return '{}'
        class Engine:model=Model()
        task,_=fixture();_write(Engine(),task,task['draft_report']['facts'])
        self.assertEqual(len(calls),1)
        self.assertIn('段落和对应的图表服务于同一个判断', CLIENT_REPORT_BRIEF)
        self.assertIn('mechanism.steps',CLIENT_REPORT_BRIEF)
        payload=json.loads(calls[0][0][1]['content'])
        self.assertIn('mechanism',payload['output_example']['logics'][0])
        self.assertIn('detail_analysis',payload['output_example']['logics'][0])
        self.assertEqual(calls[0][1]['phase'],'draft_report')

    def test_shared_date_is_a_caption_not_an_extra_static_chart_column(self):
        from researchhelper.artifact_exporter import _layout_segments
        document=design(self.content,ROOT)['document']
        def groups(blocks):
            for row in _layout_segments(blocks):
                if row['kind']=='panels':yield row
                elif row['kind']=='reading_columns':
                    for column in row['panels']:yield from groups(column)
        panels=list(groups(document['blocks']))
        group, = panels
        self.assertEqual(group['columns'], 2)
        self.assertEqual(len(group['panels']), 2)
        self.assertEqual(sum(block['kind'] == 'chart' for panel in group['panels'] for block in panel), 2)
        paper = html.document_fromstring(design(self.content, ROOT)['html'])
        dates, = paper.xpath('//p[contains(@class, "shared-figure-context")]')
        self.assertEqual(dates.text_content(), '2026-10-26至2026-10-28')

    def test_same_result_is_not_mutated_by_reuse_or_multiple_profiles(self):
        before=canonical(self.content)
        for profile in ('standard','detailed'):design(self.content,ROOT,profile=profile)
        self.assertEqual(canonical(self.content),before)
        specs=logic_figure_plan(self.content)
        self.assertEqual(len({s['target'] for s in specs}),len(specs))


def run_round(number,focus):
    """Keep ten compact receipts, not ten duplicate report directories."""
    folder=ROOT/'result/acceptance/logic-visual-upgrade';folder.mkdir(parents=True,exist_ok=True)
    log=folder/'iterations.json';records=json.loads(log.read_text()) if log.exists() else []
    if number!=len(records)+1:raise RuntimeError('Iteration receipts must be consecutive')
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(LogicVisualUpgradeTests)
    result=unittest.TextTestRunner(verbosity=0).run(suite)
    if not result.wasSuccessful():raise RuntimeError('Round checks failed')
    case=ROOT/'result/reports/gui-maotai-20261029';content=json.loads((case/'client-content.json').read_text())
    before=canonical(content);counts={}
    with patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls')):
        for profile,suffix in [('standard','对客修订版'),('detailed','内部详版')]:
            rendered=design(content,ROOT,profile=profile);tree=html.document_fromstring(rendered['html'])
            (case/('贵州茅台研究报告-'+suffix+'.html')).write_text(rendered['html'])
            counts[profile]={section.get('id'):len(section.xpath('.//figure')) for section in tree.xpath('//main//section[starts-with(@id,"logic-")]')}
    assert canonical(content)==before
    changed=['modules/visualrenderer/src/draft_visuals.py','modules/visualrenderer/src/report_designer.py',
             'modules/reportcomposer/src/drafts.py','modules/reportcomposer/src/publication_content.py',
             'products/app/frontend/visual-library.js','products/app/frontend/draft-figures.js','modules/artifactexporter/src/publication_graphics.py','modules/visualrenderer/src/visual.py','core/src/researchhelper/draft_workflow.py','core/session.py','SKILL.md']
    source_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in changed}
    if records and records[-1]['source_hashes']==source_hashes:raise RuntimeError('A new iteration requires a real implementation change')
    records.append(dict(round=number,focus=focus,tests=result.testsRun,profiles=counts,model_calls=0,data_requests=0,
                        content_unchanged=True,source_hashes=source_hashes))
    log.write_text(json.dumps(records,ensure_ascii=False,indent=2))
    print(json.dumps(dict(round=number,tests=result.testsRun,profiles=counts),ensure_ascii=False))
