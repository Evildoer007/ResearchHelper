"""App export endpoint against an isolated saved-report fixture.

Exercises the real local HTTP endpoint, byte hashes and presentation selection.
The fixture is synthetic; no provider calls or production state are involved.
"""
import base64
import csv
from io import BytesIO, StringIO
from pathlib import Path
import hashlib
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from lxml import html
from tests import ROOT
from tests.test_client_report_audience import fixture
from tests.accept_logic_visual_package import seed_copy
from researchhelper.artifact_exporter import FORMATS
from researchhelper.service import make_server
from researchhelper.report_designer import design,compatible_schemes


def export_boundary_fixture():
    """Report-only synthetic controls, signed literals and precise numbers."""
    _, content = fixture()
    content['draft']['calculations'] = []
    content['draft']['logics'] = [dict(content['draft']['logics'][0], title='原值核对',
        implication='', risk='', analysis='保留原件中甲\x01乙的标记。',
        data_refs=[], calculation_refs=[])]
    values = [
        ('\t=1+1', '-0.0080', -0.008),
        (' ＝1+1', 10000000000000003, 10000000000000003),
        ('现金\x0b流', 8.25, 8.25),
        ('+5+7', 0.12345678901234567, 0.12345678901234567),
        ('\u200b＠SUM(1,2)', 0, 0),
        ('核对",;=1+1', None, None),
    ]
    for row, (label, literal, value) in zip(content['draft']['facts'], values):
        row.update(label=label, raw_value=literal, value=value)
    return content


class ArtifactExportAPICurrent(unittest.TestCase):
    def setUp(self):
        self.workspace=tempfile.TemporaryDirectory();self.addCleanup(self.workspace.cleanup)
        self.server=make_server(self.workspace.name,ROOT,offline=True)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()
        self.addCleanup(self.close)
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.token=self.get('/api/bootstrap')['token']
        _,self.content=fixture()
        created=self.server.agent.create(self.content['question'],entry=self.content['entry'],intent=self.content['intent'],objects=self.content['objects'],research_mode='current')
        self.task=seed_copy(self.server.agent.archive,created,self.content,ROOT)
        self.presentation=design(self.server.agent.export(self.task['id'])['content'],ROOT)['document']
        self.selected={'theme':'research','schemes':{target:compatible_schemes(spec)[-1] for target,spec in self.presentation['visual_sources'].items()}}
    def close(self):
        self.server.shutdown();self.server.server_close();self.worker.join(timeout=2)
    def get(self,path):
        with urllib.request.urlopen(self.base+path,timeout=20) as response:return json.load(response)
    def export(self,profile):
        request=urllib.request.Request(self.base+'/api/export',data=json.dumps(profile).encode(),headers={'Content-Type':'application/json','X-ResearchHelper-Token':self.token})
        with urllib.request.urlopen(request,timeout=20) as response:return json.load(response)
    def test_all_formats_retain_version_profile_and_complete_visual_choice(self):
        before=self.server.agent.archive.get(self.task['id'])
        for format,prefix in [('html',b'<!doctype html>'),('pdf',b'%PDF-'),('docx',b'PK'),('xlsx',b'PK'),('csv',None)]:
            with self.subTest(format=format):
                result=self.export(dict(task_id=self.task['id'],version=1,profile='standard',format=format,visual_profile=self.selected))
                raw=base64.b64decode(result['content_base64'],validate=True)
                self.assertEqual(result['task_id'],self.task['id']);self.assertEqual(result['version'],1)
                self.assertEqual(result['profile'],'standard');self.assertEqual(result['visual_profile'],self.selected)
                self.assertEqual(result['format'],format);self.assertEqual(result['mime'],FORMATS[format])
                self.assertTrue(result['filename'].endswith('_v1_标准报告.'+format))
                self.assertEqual(hashlib.sha256(raw).hexdigest(),result['sha256'])
                if prefix:self.assertTrue(raw.startswith(prefix))
                if format=='html':
                    tree=html.document_fromstring(raw);frozen=json.loads(tree.get_element_by_id('rh-publication-document').text)
                    self.assertEqual(frozen['visual_profile'],self.selected)
                    self.assertEqual(frozen['dataset'],self.presentation['dataset'])
        self.assertEqual(self.server.agent.archive.get(self.task['id']),before)
        self.assertEqual(before['ledger']['used']['model_calls'],0);self.assertEqual(before['ledger']['used']['data_requests'],0)
    def test_special_fields_export_and_keep_the_frozen_data_exact(self):
        from docx import Document
        from openpyxl import load_workbook
        content = export_boundary_fixture()
        created = self.server.agent.create(content['question'], entry=content['entry'],
            objects=content['objects'], research_mode='current')
        task = seed_copy(self.server.agent.archive, created, content, ROOT)
        before = self.server.agent.export(task['id'])
        files = {}
        for format in ('html', 'xlsx', 'csv', 'docx'):
            receipt = self.export(dict(task_id=task['id'], version=1, profile='detailed', format=format))
            files[format] = base64.b64decode(receipt['content_base64'], validate=True)
            self.assertEqual(hashlib.sha256(files[format]).hexdigest(), receipt['sha256'])

        paper = html.document_fromstring(files['html'])
        raw = json.loads(paper.get_element_by_id('rh-publication-document').text)['dataset']['rows']
        self.assertEqual(raw[0][1:4], ['\t=1+1', '20241231', '-0.0080'])
        self.assertEqual(raw[1][3], 10000000000000003)
        self.assertEqual(raw[2][1], '现金\x0b流')

        workbook = load_workbook(BytesIO(files['xlsx']))
        sheet = workbook['研究数据']
        self.assertEqual((sheet['D3'].value, sheet['D3'].data_type), ('10000000000000003', 's'))
        self.assertEqual((sheet['D4'].value, sheet['D4'].data_type), (8.25, 'n'))
        self.assertEqual(sheet['D5'].value, str(content['draft']['facts'][3]['raw_value']))
        self.assertEqual(sheet['D6'].value, 0)
        self.assertIsNone(sheet['D7'].value)
        self.assertEqual([sheet[f'B{i}'].value for i in range(2, 8)],
            ['\t=1+1', ' ＝1+1', r'现金\u000B流', '+5+7', '\u200b＠SUM(1,2)', '核对",;=1+1'])
        self.assertTrue(all(cell.data_type != 'f' for row in sheet for cell in row))

        rows = list(csv.reader(StringIO(files['csv'].decode('utf-8-sig'))))
        self.assertEqual([row[1] for row in rows[1:7]],
            ['\\\t=1+1', '\\ ＝1+1', r'现金\u000B流', '\\+5+7', '\\\u200b＠SUM(1,2)', '核对",;=1+1'])
        self.assertEqual([row[3] for row in rows[1:7]],
            ['-0.0080', '10000000000000003', '8.25', sheet['D5'].value, '0', ''])
        self.assertTrue(all(len(row) == 6 for row in rows))

        document = Document(BytesIO(files['docx']))
        self.assertTrue(any(r'甲\u0001乙' in paragraph.text for paragraph in document.paragraphs))
        self.assertEqual(before, self.server.agent.export(task['id']))
        self.assertEqual(task['ledger']['used']['model_calls'], 0)
        self.assertEqual(task['ledger']['used']['data_requests'], 0)

    def test_prose_and_figure_columns_survive_html_first_conversion(self):
        from researchhelper.artifact_exporter import _layout_segments
        document=design(self.content,ROOT)['document']
        grouped=list(_layout_segments(document['blocks']))
        reading=[block for block in grouped if block['kind']=='reading_columns']
        self.assertEqual(len(reading),2)
        self.assertTrue(all(block['columns']==2 for block in reading))
        for block in reading:
            self.assertTrue(any(row['kind']=='paragraph' for row in block['panels'][0]))
            self.assertTrue(any(row['kind']=='chart' for row in block['panels'][1]))
        def leaves(blocks):
            for block in _layout_segments(blocks):
                if block['kind'] in ('panels','reading_columns'):
                    for panel in block['panels']:yield from leaves(panel)
                else:yield block
        projected=list(leaves(document['blocks']))
        for kind in ('paragraph','chart','caption','heading','title'):
            self.assertEqual([row.get('text',row.get('spec')) for row in projected if row['kind']==kind],
                             [row.get('text',row.get('spec')) for row in document['blocks'] if row['kind']==kind])

    def test_negative_lollipop_label_stays_above_its_point(self):
        from unittest.mock import Mock
        from researchhelper.publication_graphics import EvidenceChart
        canvas=Mock()
        chart=EvidenceChart(dict(scheme='lollipop',data=dict(labels=['经营现金流'],values=[-33.46],unit='%')),width=250,height=96)
        chart.canv=canvas;chart.draw()
        category=next(call.args for call in canvas.drawRightString.call_args_list if call.args[2]=='经营现金流')
        value=next(call.args for call in canvas.drawCentredString.call_args_list if call.args[2]=='-33.46%')
        self.assertGreater(value[1],category[1]);self.assertGreater(value[0],59)

    def test_foreign_chart_target_rejected_without_changing_saved_report(self):
        before=self.server.agent.export(self.task['id'])
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.export(dict(task_id=self.task['id'],profile='standard',format='pdf',visual_profile={'schemes':{'foreign-task-figure':'line'}}))
        self.assertEqual(failure.exception.code,400)
        self.assertEqual(self.server.agent.export(self.task['id']),before)

    def test_repeated_prepared_downloads_leave_no_copies_in_the_archive(self):
        from unittest.mock import patch
        from researchhelper import artifact_exporter

        before = self.server.agent.export(self.task['id'])
        archive = self.server.agent.archive.root / 'exports' / self.task['id']
        archive.mkdir(parents=True)
        prior = archive / 'previous.html'
        prior.write_bytes(b'User retained export')
        directories = []
        export_report = artifact_exporter.export_report

        def observe(content, resources, directory, **options):
            directories.append(Path(directory))
            receipt = export_report(content, resources, directory, **options)
            self.assertTrue(Path(receipt['source_html']).is_file())
            return receipt

        request = dict(task_id=self.task['id'], version=1, profile='detailed', format='html')
        with patch.object(artifact_exporter, 'export_report', side_effect=observe):
            results = [self.export(request), self.export(request)]
        self.assertEqual(results[0]['sha256'], results[1]['sha256'])
        self.assertEqual(len(directories), 2)
        self.assertTrue(all(not directory.exists() for directory in directories))
        self.assertEqual(list(archive.iterdir()), [prior])
        self.assertEqual(prior.read_bytes(), b'User retained export')
        self.assertEqual(self.server.agent.export(self.task['id']), before)

    def test_conversion_failure_clears_the_prepared_download_directory(self):
        from unittest.mock import patch
        from researchhelper import artifact_exporter
        from researchhelper.contracts import ContractError

        before = self.server.agent.export(self.task['id'])
        staging = []

        def fail(saved_html, *_args, **_kwargs):
            staging.append(Path(saved_html).parent.parent)
            self.assertTrue(Path(saved_html).is_file())
            raise ContractError('Controlled conversion failure')

        with patch.object(artifact_exporter, 'convert_saved_html', side_effect=fail):
            with self.assertRaises(urllib.error.HTTPError) as failure:
                self.export(dict(task_id=self.task['id'], version=1, format='docx'))
        self.assertEqual(failure.exception.code, 400)
        self.assertEqual(len(staging), 1)
        self.assertFalse(staging[0].exists())
        self.assertEqual(self.server.agent.export(self.task['id']), before)

class WorkbookTextLimitTests(unittest.TestCase):
    def test_text_fragments_keep_full_values_and_link_to_their_own_cells(self):
        from openpyxl import load_workbook
        from researchhelper.artifact_exporter import _tabular

        original = '=' + '条款甲乙丙' * 7200 + '\x01结尾'
        astral = '𠮷' * 16384
        many_lines = '第一项\n' * 254 + '最后一项'
        calculations = [dict(label=many_lines, method='raw_difference_v1', value='-0.0080', unit='元')]
        rows = [['600519.SH', original, '20241231', astral, '元', '材料']]
        data = _tabular(dict(export_rows=rows, export_calculations=calculations), 'xlsx')
        with BytesIO(data) as stream:
            workbook = load_workbook(stream)
            self.addCleanup(workbook.close)
            self.assertEqual(workbook.sheetnames, ['研究数据', '计算依据', '长文本原文'])
            self.assertEqual(workbook['研究数据']['C2'].value, '20241231')
            self.assertEqual(workbook['计算依据']['C2'].value, '-0.0080')
            fragments = workbook['长文本原文']
            self.assertEqual(tuple(cell.value for cell in fragments[1]), ('工作表', '单元格', '片段', '原文'))
            for tab, coordinate, expected in (
                ('研究数据', 'B2', original.replace('\x01', r'\u0001')),
                ('研究数据', 'D2', astral), ('计算依据', 'A2', many_lines),
            ):
                with self.subTest(tab=tab, coordinate=coordinate):
                    cell = workbook[tab][coordinate]
                    self.assertEqual(cell.value, '查看原文')
                    self.assertIsNone(cell.hyperlink.target)
                    start = int(cell.hyperlink.location.removeprefix("'长文本原文'!A"))
                    self.assertEqual(fragments[f'B{start}'].hyperlink.location, f"'{tab}'!{coordinate}")
                    matches = [row for row in fragments.iter_rows(min_row=2)
                               if (row[0].value, row[1].value) == (tab, coordinate)]
                    self.assertEqual(matches[0][0].row, start)
                    self.assertEqual([row[2].value for row in matches], list(range(1, len(matches) + 1)))
                    self.assertEqual(''.join(row[3].value for row in matches), expected)
                    self.assertTrue(all(row[3].alignment.wrap_text and row[3].data_type == 's' for row in matches))
            self.assertFalse(any(cell.data_type == 'f' for tab in workbook for row in tab for cell in row))

    def test_supported_cell_text_limits_leave_ordinary_workbook_shape_unchanged(self):
        from openpyxl import load_workbook
        from researchhelper.artifact_exporter import _tabular

        limit_text = '原' * 32767
        limit_lines = '段\n' * 253 + '尾'
        data = _tabular(dict(export_rows=[['000001.SH', limit_text, '20251101', limit_lines, '', '原件']]), 'xlsx')
        with BytesIO(data) as stream:
            workbook = load_workbook(stream)
            self.addCleanup(workbook.close)
            self.assertEqual(workbook.sheetnames, ['研究数据'])
            self.assertEqual(workbook.active['B2'].value, limit_text)
            self.assertEqual(workbook.active['D2'].value, limit_lines)


if __name__=='__main__':unittest.main()
