"""Current export contracts, preserving the earlier fixed-three-chart suite."""
import csv,hashlib,io,json,socket,unicodedata,zipfile
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from lxml import html
from pypdf import PdfReader
from tests import ROOT
from tests import test_publication_delivery as legacy
from tests import test_logic_visual_upgrade as visual_legacy
from researchhelper.artifact_exporter import export_report
from researchhelper.contracts import canonical
from researchhelper.report_designer import design


class CurrentPublicationDeliveryTests(legacy.PublicationDeliveryTests):
    def test_profiles_preserve_all_research_and_use_same_chart_plan(self):
        before=canonical(self.content);standard=design(self.content,ROOT);detailed=design(self.content,ROOT,profile='detailed')
        self.assertEqual(canonical(self.content),before)
        self.assertEqual(standard['document']['content_hash'],detailed['document']['content_hash'])
        self.assertGreater(len(detailed['document']['blocks']),len(standard['document']['blocks']))
        logic_ids={'logic-'+str(index) for index in range(1,len(self.content['draft']['logics'])+1)}
        fact_ids={row['id'] for row in self.content['draft']['facts']}
        for profile in (standard,detailed):
            self.assertIn('report-toc',profile['html']);self.assertIn('210mm',profile['html'])
            coverage=profile['document']['figure_coverage'];self.assertEqual(set(coverage),logic_ids)
            for row in coverage.values():
                self.assertIn(row['status'], ('numeric', 'numeric_text', 'numeric_table'))
                self.assertTrue(row['data_refs'])
                self.assertTrue(set(row['data_refs']) <= fact_ids)
                if row['status'] == 'numeric':
                    self.assertGreater(row['figures'], 0)
            for block in profile['document']['blocks']:
                if block['kind']=='chart':self.assertTrue(set(block['spec']['data_refs'])<=fact_ids)
            for logic in self.content['draft']['logics']:self.assertIn(logic['analysis'],profile['html'])
            for risk in self.content['draft']['risks']:self.assertIn(risk,profile['html'])

    def test_five_real_files_without_network_and_exact_source_sidecars(self):
        facts=self.content['draft']['facts']
        with TemporaryDirectory() as folder,patch.object(socket.socket,'connect',side_effect=AssertionError('Offline only')):
            for format in ('html','pdf','docx','xlsx','csv'):
                result=export_report(self.content,ROOT,folder,profile='detailed',format=format)
                path=Path(result['path']);raw=path.read_bytes();source=Path(result['source_html'])
                self.assertEqual(hashlib.sha256(raw).hexdigest(),result['sha256']);self.assertEqual(result['model_calls'],0)
                self.assertTrue(result['converted_from_saved_html']);self.assertTrue(source.is_file())
                self.assertEqual(json.loads(path.with_suffix('.json').read_text()),self.content)
                if format=='pdf':
                    reader=PdfReader(path);self.assertGreater(len(reader.pages),0)
                    # CoreText may expose equivalent Kangxi radicals in ToUnicode.
                    # Compare normalized text; physical layout remains separately checked.
                    text=unicodedata.normalize('NFKC',''.join(page.extract_text() or '' for page in reader.pages))
                    self.assertIn('核心结论',text);self.assertIn('营业收入',text)
                    self.assertTrue(all(abs(float(page.mediabox.width)-595.276)<1 for page in reader.pages))
                if format=='docx':
                    tree=html.document_fromstring(source.read_text());expected=len(tree.xpath('//main//*[contains(concat(" ",normalize-space(@class)," ")," chart ")]'))
                    self.assertGreater(expected,0)
                    with zipfile.ZipFile(path) as archive:
                        xml=archive.read('word/document.xml').decode();self.assertIn('oMath',xml);self.assertIn('<m:f>',xml)
                        images=[name for name in archive.namelist() if name.startswith('word/media/')]
                        self.assertEqual(len(images),expected)
                        self.assertTrue(all(len(archive.read(name))>16 for name in images))
                if format=='xlsx':
                    from openpyxl import load_workbook
                    book=load_workbook(path,data_only=False);sheet=book['研究数据'];self.assertEqual(sheet.max_row,len(facts)+1)
                    for index,fact in enumerate(facts,2):self.assertEqual(sheet.cell(index,4).value,fact.get('raw_value',fact['value']))
                if format=='csv':
                    rows=list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))));self.assertEqual(len(rows),len(facts)+1)
                    for row,fact in zip(rows[1:],facts):self.assertEqual(Decimal(row[3]),Decimal(str(fact.get('raw_value',fact['value']))))
        self.assertEqual(hashlib.sha256(legacy.CONTENT.read_bytes()).hexdigest(),self.source_hash)


class CurrentLogicVisualUpgradeTests(visual_legacy.LogicVisualUpgradeTests):
    def test_cash_logic_has_its_own_emphasized_registered_observation(self):
        page = html.document_fromstring(design(self.content, ROOT)['html'])
        argument = page.get_element_by_id('logic-2')
        self.assertFalse(argument.xpath('.//figure'))
        reading, = argument.xpath('.//*[@data-calculation-ref]/a')
        self.assertEqual(reading.text_content(), '-33.33%')
        source = page.get_element_by_id(reading.get('href')[1:])
        refs = reading.get('data-inspection-observation').split()
        self.assertEqual(source.xpath('.//tr/@data-observation-id'), refs)
        rows = {row['id']: row for row in self.content['draft']['facts']}
        self.assertEqual({rows[ref]['field'] for ref in refs}, {'ths_ncf_from_oa_stock'})
