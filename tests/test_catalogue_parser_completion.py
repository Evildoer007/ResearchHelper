"""Current contract completeness and text-only PDF recovery; no external calls."""
from copy import deepcopy
import hashlib,socket,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import data_request_contract,extract_material
from researchhelper.planning import question_specs
from researchhelper.reasoning import validate_plan
from researchhelper.knowledge import ENTRIES,RETAINED_VERSIONS,ResearchKnowledge
from researchhelper.material_parser import MaterialParser,verified_material
from tests.test_provenance_parser_offline import publication_pdf
from core.session import Session


class CatalogueParserCompletionTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external networking'))
        guard.start();self.addCleanup(guard.stop)

    def test_descriptors_are_uniform_without_inventing_official_selections(self):
        with tempfile.TemporaryDirectory(prefix='rh-descriptor-') as folder:
            agent=ResearchAgent(ResearchArchive(folder),ROOT,offline=True)
            try:
                task=agent.create('核对600000.SH资料',entry='事件',objects=['600000.SH'],
                    cutoff='2026-10-01T12:00:00+08:00',questions=[dict(id='q1',question='资料内容是什么',kind='fact')])
                before=deepcopy(task);contract=data_request_contract(task)
                self.assertTrue(contract['tools'])
                for tool in contract['tools']:
                    self.assertIn('example',tool)
                    if tool['example']:
                        validate_plan(task,dict(questions=question_specs(task),data_requests=[tool['example']],calculations=[]))
                    if tool['operation'] in ('release_catalog','release_original'):
                        self.assertIsNone(tool['example'])
                self.assertEqual(task,before)
                self.assertEqual(task['ledger']['used']['model_calls'],0)
                self.assertEqual(task['ledger']['used']['data_requests'],0)
            finally:agent.close_runtime()

    def test_pmi_metadata_revision_keeps_content_and_retained_v1(self):
        old=deepcopy(RETAINED_VERSIONS[('rh_k_pmi_diffusion',1)])
        current=next(entry for entry in ENTRIES if entry['id']=='rh_k_pmi_diffusion')
        self.assertEqual(current['version'],2);self.assertEqual(current['previous_version'],1)
        for key in ('content','conditions','counterexample','source_reference','reasoning_checks'):
            self.assertEqual(current[key],old[key])
        self.assertNotIn('publication_date',old)
        self.assertIsNone(current['publication_date']);self.assertIn('未知',current['publication_date_status'])
        self.assertTrue(current['verification_indicators'] and current['dependencies'])
        self.assertIn('不是历史当时规则',current['temporal_scope'])
        knowledge=ResearchKnowledge();package=knowledge.read('宏观','PMI',context={'knowledge_catalogue_revision':2})
        self.assertEqual(package['index']['rh_k_pmi_diffusion'],2)
        retained=deepcopy(package)
        retained['entries']=[old if row['id']==old['id'] else row for row in retained['entries']]
        retained['index'][old['id']]=1
        knowledge.validate_package(retained)
        self.assertEqual(RETAINED_VERSIONS[(old['id'],1)],old)

    def test_layout_failure_keeps_literal_layer_without_fabricating_tables(self):
        payload=publication_pdf();material=extract_material('original.pdf',payload)
        self.assertEqual(material['hash'],hashlib.sha256(payload).hexdigest())
        self.assertEqual(material['status'],'已解析')
        self.assertIn('合成原件，发布2026-09-29。',material['parts'][0]['text'])
        self.assertTrue(material['parts'][0]['pdf_layout_limited'])
        self.assertFalse(any('pdf_table' in part for part in material['parts']))
        self.assertIn('文字层',material['limitation'])
        self.assertEqual((material['source_check'],material['applicability_check']),('待核验','待核验'))
        self.assertEqual(extract_material('original.pdf',payload)['parts'],material['parts'])

    def test_isolated_parser_and_skill_upload_preserve_the_same_original(self):
        payload=publication_pdf();parsed=MaterialParser().parse('original.pdf',payload)
        self.assertEqual(parsed['status'],'已解析');self.assertTrue(parsed['parse_runtime']['parser_worker_reaped'])
        self.assertEqual(verified_material(parsed,payload)['parts'],parsed['parts'])
        with tempfile.TemporaryDirectory(prefix='rh-upload-') as folder:
            session=Session(folder)
            try:
                task=session.create('仅核对原件写了什么')
                material=session.material(task['id'],'original.pdf',payload)
                self.assertEqual(material['hash'],parsed['hash'])
                catalog=session.sources(task['id'])
                self.assertEqual(catalog['items'][0]['parse_limitation'],parsed['limitation'])
                item=catalog['items'][0]
                request=dict(item['request_binding'],queries=['合成原件'],parts=item['reading_spans'],reason='核对原件文字层',question_ids=[task['questions'][0]['id']])
                reading=session.read(task['id'],[request],catalog['binding'])
                self.assertIn('合成原件',str(reading))
                current=session.status(task['id'])
                self.assertEqual(current['ledger']['used']['model_calls'],0)
                self.assertEqual(current['ledger']['used']['data_requests'],0)
                self.assertFalse(current['evidence'] or current['theses'])
            finally:session.agent.close_runtime()

    def test_bad_layout_page_is_closed_before_the_next_page_and_cannot_add_a_grid(self):
        from pdfplumber.utils.exceptions import PdfminerException
        from researchhelper.pdf_material import extract_parts
        pages=[]
        class Page:
            def __init__(self,index):self.index,self.closed=index,False
            @property
            def lines(self):
                if self.index==0:raise PdfminerException('Controlled layout failure')
                return []
            def close(self):self.closed=True
        class Document:
            def __enter__(self):return self
            def __exit__(self,*args):return False
        pages.extend([Page(0),Page(1)]);document=Document();document.pages=pages
        def second(**_):
            self.assertTrue(pages[0].closed)
            return '可读的第二页'
        plain=[SimpleNamespace(extract_text=lambda **_:'可读的第一页'),SimpleNamespace(extract_text=second)]
        with patch('pypdf.PdfReader',return_value=SimpleNamespace(pages=plain)),patch('pdfplumber.open',return_value=document):
            parts=extract_parts(b'controlled page lifetime fixture')
        self.assertTrue(all(page.closed for page in pages))
        self.assertEqual([part['text'] for part in parts],['可读的第一页','可读的第二页'])
        self.assertTrue(parts[0]['pdf_layout_limited']);self.assertNotIn('pdf_layout_limited',parts[1])
        self.assertFalse(any('pdf_table' in part for part in parts))

    def test_blank_scan_does_not_gain_an_excerpt(self):
        from io import BytesIO
        from pypdf import PdfWriter
        output=BytesIO();writer=PdfWriter();writer.add_blank_page(width=600,height=300);writer.write(output)
        material=extract_material('scan.pdf',output.getvalue())
        self.assertEqual(material['status'],'解析受限')
        self.assertFalse(any(part['text'].strip() for part in material['parts']))
        self.assertIn('不生成伪造摘录',material['limitation'])


class MaterialHTTPUploadTests(unittest.TestCase):
    """Exercise native service routes with its authenticated, isolated parser."""

    def setUp(self):
        import threading
        from researchhelper.service import make_server
        self.folder=tempfile.TemporaryDirectory(prefix='rh-http-original-')
        self.addCleanup(self.folder.cleanup)
        self.server=make_server(self.folder.name,ROOT,offline=True,authentication=True,
            preset_accounts=True,runtime_configurable=True)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start();self.addCleanup(self.close_server)
        self.cookie='';self.token=''
        status,value=self.request('/api/auth/status')
        self.assertEqual(status,200);self.token=value['token']
        status,_=self.request('/api/auth/login',dict(account='admin',password='66666666',remember=False))
        self.assertEqual(status,200)
        from researchhelper.contracts import DOCUMENT_CONTENT_INTENT
        status,self.task=self.request('/api/tasks',dict(question='仅核对本地原件',intent=DOCUMENT_CONTENT_INTENT,
            questions=[dict(id='q1',question='原件写了什么',kind='fact',required=True)]))
        self.assertEqual(status,200)
        self.route='/api/tasks/'+self.task['id']

    def close_server(self):
        self.server.shutdown();self.server.server_close();self.worker.join(timeout=3)
        self.assertFalse(self.worker.is_alive())

    def request(self,route,payload=None):
        import http.client,json
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=20)
        headers={'Cookie':self.cookie}
        if payload is not None:
            headers.update({'Content-Type':'application/json','X-ResearchHelper-Token':self.token})
        try:
            connection.request('POST' if payload is not None else 'GET',route,
                body=json.dumps(payload) if payload is not None else None,headers=headers)
            response=connection.getresponse()
            if response.getheader('Set-Cookie'):
                self.cookie=response.getheader('Set-Cookie').split(';')[0]
            return response.status,json.loads(response.read())
        finally:connection.close()

    def encoded(self,name,payload):
        import base64
        return dict(name=name,base64=base64.b64encode(payload).decode(),expected_version=self.task['version'])

    def test_authenticated_pdf_upload_can_be_catalogued_and_read(self):
        payload=publication_pdf()
        status,task=self.request(self.route+'/material',self.encoded('original.pdf',payload))
        self.assertEqual(status,200,task)
        material=task['materials'][-1]
        self.assertEqual(material['hash'],hashlib.sha256(payload).hexdigest())
        self.assertTrue(material['parts'][0]['pdf_layout_limited'])
        status,catalog=self.request(self.route+'/source-catalog',{})
        self.assertEqual(status,200,catalog);item=catalog['items'][0]
        self.assertEqual(item['parse_limitation'],material['limitation'])
        request=dict(item['request_binding'],queries=['合成原件'],parts=item['reading_spans'],
            reason='核对原件文字层',question_ids=['q1'])
        status,reading=self.request(self.route+'/read-materials',dict(requests=[request],binding=catalog['binding']))
        self.assertEqual(status,200,reading);self.assertIn('合成原件',str(reading))
        _,current=self.request(self.route)
        self.assertEqual(current['ledger']['used']['model_calls'],0)
        self.assertEqual(current['ledger']['used']['data_requests'],0)
        self.assertFalse(current['evidence'] or current['theses'])

    def test_authenticated_filing_route_retains_supplied_original_bytes(self):
        payload='合成政策原件，仅用于本地接口验收。'.encode()
        directory=dict(issuer='材料示例政策',source='独立验收文件',source_id='fixture-http-policy',
            title='政策原件',published_at=None,original_url='https://example.invalid/policy.txt',
            query_coverage=dict(code='600000.SH',start='2026-07-01',end='2026-07-20'))
        request=self.encoded('policy.txt',payload);request['directory']=directory
        status,task=self.request(self.route+'/filing',request)
        self.assertEqual(status,200,task)
        material=task['source_records'][-1]['snapshot']['material']
        self.assertEqual(material['hash'],hashlib.sha256(payload).hexdigest())
        self.assertIn('合成政策原件',material['parts'][0]['text'])
        self.assertEqual(material['source_check'],'待核验')
        self.assertEqual(task['ledger']['used']['data_requests'],0)

    def test_malformed_base64_is_a_client_error_without_a_new_original(self):
        status,error=self.request(self.route+'/material',dict(name='bad.txt',base64='!invalid!',
            expected_version=self.task['version']))
        self.assertEqual(status,400,error)
        _,task=self.request(self.route)
        self.assertFalse(task['materials'])
        self.assertFalse(self.server.agent.archive.retained_originals(task))

    def test_report_export_and_material_upload_both_work_on_the_same_service(self):
        import base64,time
        status,task=self.request('/api/cases',dict(id='etf'));self.assertEqual(status,200,task)
        _,bootstrap=self.request('/api/bootstrap')
        expected={key:bootstrap['execution'][key] for key in ('mode','policy_version')}
        route='/api/tasks/'+task['id']
        status,task=self.request(route+'/start',dict(expected_execution=expected));self.assertEqual(status,200,task)
        deadline=time.monotonic()+20
        while not task.get('delivery') and task['status']=='运行中' and time.monotonic()<deadline:
            time.sleep(.02);_,task=self.request(route)
        self.assertTrue(task.get('delivery'),task.get('error'))
        status,export=self.request('/api/export',dict(task_id=task['id'],profile='standard',format='html'))
        self.assertEqual(status,200,export)
        raw=base64.b64decode(export['content_base64'],validate=True)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),export['sha256'])
        status,uploaded=self.request(self.route+'/material',self.encoded('note.txt','本地补充原件。'.encode()))
        self.assertEqual(status,200,uploaded)
        self.assertEqual(uploaded['materials'][-1]['parts'][0]['text'],'本地补充原件。')


if __name__=='__main__':unittest.main()
