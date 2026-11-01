"""Independent acceptance for one research version and five real export formats."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import csv,hashlib,io,json,socket,unittest,zipfile
from tests import ROOT
from researchhelper.artifact_exporter import export_report,data_rows
from researchhelper.report_designer import design
from researchhelper.contracts import canonical,ContractError
from pypdf import PdfReader

CONTENT=ROOT/'result/acceptance/one-sentence-research-vgjkyxq9/report.json'


class PublicationDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.content=json.loads(CONTENT.read_text());cls.source_hash=hashlib.sha256(CONTENT.read_bytes()).hexdigest()
    def test_profiles_preserve_all_research_and_use_same_chart_plan(self):
        before=canonical(self.content);standard=design(self.content,ROOT);detailed=design(self.content,ROOT,profile='detailed')
        self.assertEqual(canonical(self.content),before)
        self.assertEqual(standard['document']['content_hash'],detailed['document']['content_hash'])
        self.assertGreater(len(detailed['document']['blocks']),len(standard['document']['blocks']))
        for profile in [standard,detailed]:
            self.assertTrue('report-toc' in profile['html'],'报告应使用券商样例的小目录');self.assertTrue('210mm' in profile['html'])
            self.assertEqual(len([b for b in profile['document']['blocks'] if b['kind']=='chart']),3)
            for logic in self.content['draft']['logics']:self.assertIn(logic['analysis'],profile['html'])
            for risk in self.content['draft']['risks']:self.assertIn(risk,profile['html'])
    def test_five_real_files_without_network_and_exact_source_sidecars(self):
        with TemporaryDirectory() as folder,patch.object(socket.socket,'connect',side_effect=AssertionError('Offline only')):
            for format in ['html','pdf','docx','xlsx','csv']:
                result=export_report(self.content,ROOT,folder,profile='detailed',format=format);path=Path(result['path']);raw=path.read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(),result['sha256']);self.assertEqual(result['model_calls'],0);self.assertTrue(result['converted_from_saved_html']);self.assertTrue(Path(result['source_html']).is_file())
                self.assertEqual(json.loads(path.with_suffix('.json').read_text()),self.content)
                if format=='pdf':
                    reader=PdfReader(path);self.assertGreater(len(reader.pages),0)
                    text=''.join(page.extract_text() for page in reader.pages);self.assertIn('核心结论',text);self.assertIn('营业收入',text)
                    self.assertTrue(all(abs(float(page.mediabox.width)-595.276)<1 for page in reader.pages))
                if format=='docx':
                    with zipfile.ZipFile(path) as archive:
                        xml=archive.read('word/document.xml').decode();self.assertIn('oMath',xml)
                        self.assertIn('<m:f>',xml);self.assertEqual(len([n for n in archive.namelist() if n.startswith('word/media/')]),3)
                if format=='xlsx':
                    from openpyxl import load_workbook
                    book=load_workbook(path,data_only=False);self.assertEqual(book['研究数据'].max_row,11)
                    self.assertEqual(book['研究数据']['D2'].value,self.content['draft']['facts'][0]['value'])
                if format=='csv':self.assertEqual(len(list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))),11)
        self.assertEqual(hashlib.sha256(CONTENT.read_bytes()).hexdigest(),self.source_hash)
    def test_html_is_the_saved_conversion_source_and_details_stay_in_each_logic(self):
        from lxml import html
        from researchhelper.artifact_exporter import convert_saved_html
        detailed=design(self.content,ROOT,profile='detailed')
        tree=html.document_fromstring(detailed['html'])
        details=tree.xpath('//*[@data-logic-detail]')
        self.assertEqual(len(details),len(self.content['draft']['logics']))
        for section in details:
            self.assertEqual(section.getparent().get('id'),section.get('data-logic-detail'))
        with TemporaryDirectory() as folder:
            result=export_report(self.content,ROOT,folder,format='pdf')
            source=Path(result['source_html']);source.write_text(source.read_text()+'<!--changed-->')
            with self.assertRaises(ContractError):
                convert_saved_html(source,'pdf',expected_sha256=result['source_html_sha256'])

    def test_unsupported_requests_do_not_write_or_invalidate_original(self):
        with TemporaryDirectory() as folder:
            for changes in [dict(format='exe'),dict(profile='secret')]:
                with self.assertRaises(ContractError):export_report(self.content,ROOT,folder,**changes)
            self.assertEqual(list(Path(folder).iterdir()),[])
    def test_public_appendix_does_not_copy_private_source_snapshots(self):
        content=json.loads(CONTENT.read_text());content['source_records'][0]['snapshot']['test_secret']='PRIVATE_EXPORT_MARKER'
        # A source snapshot is intentionally outside the public appendix projection.
        from researchhelper.report_designer import _public_appendix
        self.assertNotIn('PRIVATE_EXPORT_MARKER',_public_appendix(content))
    def test_csv_and_workbook_do_not_execute_source_strings(self):
        content=json.loads(CONTENT.read_text());content['draft']['facts'][0]['label']='=2+2'
        from researchhelper.artifact_exporter import _tabular
        self.assertIn("'=2+2",_tabular(content,'csv').decode('utf-8-sig'))
        from openpyxl import load_workbook
        book=load_workbook(io.BytesIO(_tabular(content,'xlsx')));self.assertEqual(book.active['B2'].data_type,'s')
    def test_legacy_html_export_is_not_overwritten_by_new_formats(self):
        with TemporaryDirectory() as folder:
            first=export_report(self.content,ROOT,folder);before=Path(first['path']).read_bytes()
            second=export_report(self.content,ROOT,folder,format='pdf')
            self.assertNotEqual(first['path'],second['path']);self.assertEqual(Path(first['path']).read_bytes(),before)


if __name__=='__main__':unittest.main()
