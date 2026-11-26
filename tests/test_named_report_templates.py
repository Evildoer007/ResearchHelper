"""Current report names; inherit unchanged data, conversion and math acceptance."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json,sys
from lxml import html
from tests import ROOT
from tests import test_strategy_paper_template as previous
from researchhelper.report_designer import design,report_template
from researchhelper.artifact_exporter import capabilities,export_report
from researchhelper.contracts import ContractError

class NamedReportTemplatesTests(previous.StrategyPaperTemplateTests):
    def test_real_html_template_is_a_bundled_build_input(self):
        assets=ROOT/'modules/visualrenderer/assets';names=['report.html','evidence-charts.js','templates/report-standard.template.json','templates/report-detailed.template.json']
        registered=json.loads((ROOT/'packaging/package-source-map.json').read_text())['files']
        for name in names:
            self.assertTrue((assets/name).is_file());self.assertIn(str((assets/name).relative_to(ROOT)),registered)
        self.assertFalse((assets/'strategy-paper.html').exists())
        self.assertIn('data-paper-slot="logics"',(assets/'report.html').read_text())
    def test_app_and_skill_default_design_renders_only_the_scoped_figures(self):
        result=design(self.content,ROOT);tree=html.document_fromstring(result['html'])
        self.assertEqual(result['document']['template'],dict(id='report-standard',version=1));self.assertEqual(result['document']['output_type'],'report')
        self.assertEqual(len(tree.xpath('//main//figure')),3)
        self.assertEqual(len(tree.xpath('//main')),1);self.assertEqual(len(tree.xpath('//*[@id="theses"]')),1)
        self.assertEqual(len([b for b in result['document']['blocks'] if b['kind']=='chart']),3)
    def test_packagers_copy_the_single_template_and_composition_renderer(self):
        sys.path.insert(0,str(ROOT/'packaging'));from shared_payload import copy_report_design
        with TemporaryDirectory() as folder:
            destination=Path(folder)/'app/web';copy_report_design(destination)
            for name in ('report.html','evidence-charts.js','templates/report-standard.template.json','templates/report-detailed.template.json'):
                self.assertEqual((destination/'report-design'/name).read_bytes(),(ROOT/'modules/visualrenderer/assets'/name).read_bytes())
    def test_names_match_profile_definition_and_html_metadata(self):
        for profile in ('standard','detailed'):
            definition=report_template(ROOT,profile);publication=design(self.content,ROOT,profile=profile);tree=html.document_fromstring(publication['html'])
            self.assertEqual(definition['id'],'report-'+profile);self.assertEqual(definition['output_type'],'report');self.assertEqual(definition['shell'],'report.html')
            self.assertEqual(publication['document']['template']['id'],definition['id']);self.assertEqual(tree.xpath('//body/@data-report-template'),[definition['id']])
            self.assertEqual(tree.xpath('//body/@data-report-variant'),[profile]);self.assertEqual(tree.xpath('//meta[@name="rh-report-template"]/@content'),[definition['id']+'@1'])
    def test_capability_and_saved_exports_use_the_same_names(self):
        options=capabilities();self.assertEqual(options['default_template'],'report-standard')
        self.assertEqual({r['id'] for r in options['templates']},{'report-standard','report-detailed'})
        with TemporaryDirectory() as folder:
            for profile in ('standard','detailed'):
                result=export_report(self.content,ROOT,folder,profile=profile)
                self.assertEqual(result['template_id'],'report-'+profile);self.assertEqual(result['output_type'],'report')
                self.assertIn('标准报告' if profile=='standard' else '详细报告',result['filename'])
    def test_unknown_profiles_cannot_become_template_paths(self):
        for invalid in ('../report','report-standard','card-standard','',None,[]):
            with self.assertRaises(ContractError):report_template(ROOT,invalid)

if __name__=='__main__':
    import unittest
    unittest.main()
