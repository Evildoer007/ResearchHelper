import unicodedata
"""Shared broker-paper template, data-bound figure panels and real conversions."""
from pathlib import Path
from tempfile import TemporaryDirectory
from copy import deepcopy
from unittest.mock import patch
import csv,hashlib,io,json,socket,sys,unittest,zipfile,importlib.util
from lxml import html
from pypdf import PdfReader
from tests import ROOT
from researchhelper.report_designer import design
from researchhelper.draft_visuals import logic_figure_plan
from researchhelper.visual_selection import report_chart_supported
from researchhelper.artifact_exporter import export_report,convert_saved_html
from researchhelper.contracts import canonical,ContractError

class StrategyPaperTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content=json.loads((ROOT/'result/acceptance/one-sentence-research-vgjkyxq9/report.json').read_text())
        cls.reference=ROOT/'result/reports/broker-2026-07/券商板块策略报告.html';cls.reference_hash=hashlib.sha256(cls.reference.read_bytes()).hexdigest()
    def test_real_html_template_is_a_bundled_build_input(self):
        source=ROOT/'modules/visualrenderer/assets/report.html'
        self.assertTrue(source.is_file());self.assertTrue('data-paper-slot="logics"' in source.read_text())
        registered=json.loads((ROOT/'packaging/package-source-map.json').read_text())['files']
        self.assertIn(str(source.relative_to(ROOT)),registered);self.assertIn('modules/visualrenderer/assets/evidence-charts.js',registered)
    def test_reference_case_keeps_six_figures_in_readable_paper_groups(self):
        spec=importlib.util.spec_from_file_location('broker_reference',ROOT/'modules/reportcomposer/examples/broker_report.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        data=json.loads((self.reference.parent/'report-data.json').read_text())
        for profile in ('standard','detailed'):
            with self.subTest(profile=profile):
                tree=html.document_fromstring(module.render(data,profile=profile))
                self.assertEqual(tree.xpath('//body/@data-report-template'),['report-'+profile])
                self.assertEqual(len(tree.xpath('//main')),1)
                self.assertEqual(len(tree.xpath('//main//figure')),6)
                self.assertEqual(len(tree.xpath('//main//section[starts-with(@id,"logic-")]')),3)
                self.assertEqual(len(tree.xpath('//*[@data-logic-detail]')),3 if profile=='detailed' else 0)
                rows=tree.xpath('//main//*[@data-columns]')
                self.assertTrue(any(len(row.findall('figure'))==2 for row in rows))
                self.assertEqual(sum(len(row.findall('figure')) for row in rows),6)
                for row in rows:
                    self.assertEqual(int(row.get('data-columns')),len(row.findall('figure')))
                    self.assertLessEqual(int(row.get('data-columns')),2 if profile=='detailed' else 3)
        self.assertEqual(hashlib.sha256(self.reference.read_bytes()).hexdigest(),self.reference_hash)
    def test_app_and_skill_default_design_contains_bound_figures_and_sparse_tables(self):
        result=design(self.content,ROOT);tree=html.document_fromstring(result['html'])
        self.assertEqual(result['document']['template'],dict(id='report-standard',version=1))
        self.assertEqual(len(tree.xpath('//main//figure')),3)
        self.assertEqual(len(tree.xpath('//main')),1)
        self.assertEqual(len(tree.xpath('//*[@id="theses"]')),1)
        self.assertEqual(len([b for b in result['document']['blocks'] if b['kind']=='chart']),3)
        self.assertTrue(all(b.get('figure_group') for b in result['document']['blocks'] if b['kind']=='chart'))
        for spec in result['document']['visual_sources'].values():
            index=int(spec['paper_logic'].split('-')[-1])-1
            self.assertTrue(set(spec['data_refs'])<=set(self.content['draft']['logics'][index]['data_refs']))
            self.assertTrue(report_chart_supported(spec))
        self.assertEqual(result['document']['figure_coverage']['logic-3']['status'],'numeric_table')
    def test_client_and_internal_preserve_the_same_complete_research(self):
        before=canonical(self.content);client=design(self.content,ROOT);internal=design(self.content,ROOT,profile='detailed')
        self.assertEqual(canonical(self.content),before);self.assertEqual(client['document']['content_hash'],internal['document']['content_hash'])
        for result in [client,internal]:
            tree=html.document_fromstring(result['html']);text=''.join(tree.xpath('//main//text()'))
            self.assertIn(self.content['draft']['conclusion'],text)
            for logic in self.content['draft']['logics']:self.assertIn(logic['analysis'],text)
            for risk in self.content['draft']['risks']:self.assertIn(risk,text)
            self.assertEqual(tree.xpath('//details|//summary'),[])
        tree=html.document_fromstring(internal['html']);details=tree.xpath('//*[@data-logic-detail]');self.assertEqual(len(details),3)
        self.assertTrue(all(n.getparent().get('id')==n.get('data-logic-detail') for n in details))
    def test_missing_or_foreign_inputs_do_not_create_filler_charts(self):
        foreign=deepcopy(self.content)
        for source in foreign['source_records']:source['task_id']='foreign-task'
        self.assertEqual(logic_figure_plan(foreign),[])
        sparse=deepcopy(self.content)
        for logic in sparse['draft']['logics']:
            logic['data_refs']=logic['data_refs'][:1]
            logic['calculation_refs']=[]
        self.assertFalse(any(spec.get('paper_logic') for spec in logic_figure_plan(sparse)))
    def test_five_formats_convert_saved_html_with_exact_data_and_real_graphics(self):
        with TemporaryDirectory() as folder,patch.object(socket.socket,'connect',side_effect=AssertionError('Offline only')):
            for format in ['html','pdf','docx','xlsx','csv']:
                result=export_report(self.content,ROOT,folder,format=format);file=Path(result['path'])
                self.assertTrue(result['converted_from_saved_html']);self.assertEqual(result['model_calls'],0)
                self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(),result['sha256'])
                self.assertEqual(hashlib.sha256(Path(result['source_html']).read_bytes()).hexdigest(),result['source_html_sha256'])
                if format=='pdf':
                    pdf=PdfReader(file);self.assertTrue(1<=len(pdf.pages)<=3);self.assertIn('核心结论',unicodedata.normalize('NFKC',''.join(page.extract_text() for page in pdf.pages)))
                elif format=='docx':
                    with zipfile.ZipFile(file) as bundle:self.assertEqual(len([name for name in bundle.namelist() if name.startswith('word/media/')]),3)
                elif format=='xlsx':
                    from openpyxl import load_workbook
                    self.assertEqual(load_workbook(file).active.max_row,11)
                elif format=='csv':self.assertEqual(len(list(csv.reader(io.StringIO(file.read_text(encoding='utf-8-sig'))))),11)
    def test_internal_exports_preserve_real_math_and_the_three_supported_figures(self):
        with TemporaryDirectory() as folder,patch.object(socket.socket,'connect',side_effect=AssertionError('Offline only')):
            result=export_report(self.content,ROOT,folder,profile='detailed',format='pdf')
            pdf=PdfReader(result['path']);self.assertLessEqual(len(pdf.pages),8)
            result=export_report(self.content,ROOT,folder,profile='detailed',format='docx')
            with zipfile.ZipFile(result['path']) as bundle:
                self.assertIn('<m:f>',bundle.read('word/document.xml').decode())
                self.assertEqual(len([name for name in bundle.namelist() if name.startswith('word/media/')]),3)

    def test_static_formats_keep_html_figure_groups_and_order(self):
        result=design(self.content,ROOT)
        from researchhelper.artifact_exporter import _layout_segments
        def groups(blocks):
            for row in _layout_segments(blocks):
                if row['kind']=='panels':yield row
                elif row['kind']=='reading_columns':
                    for column in row['panels']:yield from groups(column)
        panels=list(groups(result['document']['blocks']))
        tree=html.document_fromstring(result['html'])
        html_groups=[(int(row.get('data-columns')),row.xpath('.//*[@role="img"]/@id'))
                     for row in tree.xpath('//main//*[@data-columns]')]
        static_groups=[(row['columns'],[block['spec']['target'] for column in row['panels']
                                        for block in column if block['kind']=='chart']) for row in panels]
        self.assertEqual(sum(len(targets) for _,targets in html_groups),3)
        self.assertEqual(static_groups,html_groups)
    def test_source_html_cannot_change_before_conversion(self):
        with TemporaryDirectory() as folder:
            result=export_report(self.content,ROOT,folder);source=Path(result['source_html']);source.write_text(source.read_text()+'changed')
            with self.assertRaises(ContractError):convert_saved_html(source,'pdf',expected_sha256=result['source_html_sha256'])
    def test_packagers_copy_the_single_template_and_composition_renderer(self):
        sys.path.insert(0,str(ROOT/'packaging'))
        from shared_payload import copy_report_design
        with TemporaryDirectory() as folder:
            destination=Path(folder)/'app/web';copy_report_design(destination)
            for name in ['report.html','evidence-charts.js']:
                self.assertEqual((destination/'report-design'/name).read_bytes(),(ROOT/'modules/visualrenderer/assets'/name).read_bytes())

if __name__=='__main__':unittest.main()
