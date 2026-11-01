"""App export endpoint against an isolated saved-report fixture.

Exercises the real local HTTP endpoint, byte hashes and presentation selection.
The fixture is synthetic; no provider calls or production state are involved.
"""
import base64
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
from researchhelper.service import make_server
from researchhelper.report_designer import design,compatible_schemes

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
                self.assertEqual(hashlib.sha256(raw).hexdigest(),result['sha256'])
                if prefix:self.assertTrue(raw.startswith(prefix))
                if format=='html':
                    tree=html.document_fromstring(raw);frozen=json.loads(tree.get_element_by_id('rh-publication-document').text)
                    self.assertEqual(frozen['visual_profile'],self.selected)
                    self.assertEqual(frozen['dataset'],self.presentation['dataset'])
        self.assertEqual(self.server.agent.archive.get(self.task['id']),before)
        self.assertEqual(before['ledger']['used']['model_calls'],0);self.assertEqual(before['ledger']['used']['data_requests'],0)
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

if __name__=='__main__':unittest.main()
