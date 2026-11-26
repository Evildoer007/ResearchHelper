"""The exact saved HTML is printed in an isolated, invisible worker."""
from pathlib import Path
import hashlib,json,subprocess,tempfile,unittest
from unittest.mock import patch
from types import SimpleNamespace
from tests import ROOT
from researchhelper.contracts import ContractError
from researchhelper.html_pdf import render_saved_html


class HTMLPDFExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.worker=self.root/'app/tools/ResearchHelperHTMLPDF'
        self.worker.parent.mkdir(parents=True);self.worker.write_bytes(b'owned native worker fixture');self.worker.chmod(0o444)
        self.source=self.root/'report.html';self.source.write_text('<main>保留的报告</main>')
        self.raw=self.source.read_bytes();self.sha=hashlib.sha256(self.raw).hexdigest()
        self.state=dict(status='saved',engine='webkit',foreground_opened=False,system_panels_shown=False,physical_job_sent=False)

    def render(self):return render_saved_html(self.source,expected_sha256=self.sha,resources=self.root)

    def success(self,arguments,**options):
        self.arguments=arguments
        self.assertEqual(Path(arguments[1]),self.source)
        self.assertEqual(options['timeout'],40)
        self.assertNotEqual(Path(arguments[0]),self.worker)
        Path(arguments[2]).write_bytes(b'%PDF-'+b'x'*1200)
        return SimpleNamespace(returncode=0,stdout=json.dumps(self.state),stderr="")

    def test_uses_exact_html_and_private_executable_copy_without_changing_installation(self):
        before=self.worker.stat()
        with patch('researchhelper.html_pdf.subprocess.run',side_effect=self.success):data=self.render()
        self.assertTrue(data.startswith(b'%PDF-'));self.assertEqual(self.source.read_bytes(),self.raw)
        self.assertEqual(self.worker.stat().st_mode,before.st_mode);self.assertEqual(self.worker.read_bytes(),b'owned native worker fixture')
        self.assertFalse(Path(self.arguments[0]).parent.exists())

    def test_changed_html_is_rejected_before_starting_worker(self):
        self.source.write_text('changed')
        with patch('researchhelper.html_pdf.subprocess.run') as run,self.assertRaises(ContractError):self.render()
        run.assert_not_called()

    def test_concurrent_html_change_cannot_be_recorded_as_the_original_pdf(self):
        def changed(arguments,**options):
            result=self.success(arguments,**options);self.source.write_text('changed during conversion');return result
        with patch('researchhelper.html_pdf.subprocess.run',side_effect=changed),self.assertRaises(ContractError):self.render()
        self.assertFalse(Path(self.arguments[0]).parent.exists())

    def test_worker_failure_or_unrequested_ui_does_not_produce_a_success(self):
        for change in ('failure','foreground_opened','system_panels_shown','physical_job_sent','engine'):
            state=dict(self.state)
            def invalid(arguments,**options):
                result=self.success(arguments,**options)
                if change=='failure':result.returncode=1
                elif change=='engine':state['engine']='other'
                else:state[change]=True
                result.stdout=json.dumps(state);return result
            with self.subTest(change=change),patch('researchhelper.html_pdf.subprocess.run',side_effect=invalid),self.assertRaises(ContractError):self.render()
            self.assertEqual(self.source.read_bytes(),self.raw)

    def test_missing_or_linked_renderer_is_not_silently_replaced_by_a_new_layout(self):
        data=self.worker.read_bytes();self.worker.unlink()
        for linked in (False,True):
            if linked:
                other=self.root/'other';other.write_bytes(data);self.worker.symlink_to(other)
            with patch('researchhelper.html_pdf.subprocess.run') as run,self.assertRaises(ContractError):self.render()
            run.assert_not_called();self.assertEqual(self.source.read_bytes(),self.raw)

    def test_timeout_leaves_the_html_and_removes_temporary_worker(self):
        with patch('researchhelper.html_pdf.subprocess.run',side_effect=subprocess.TimeoutExpired('worker',40)),self.assertRaises(ContractError):self.render()
        self.assertEqual(self.source.read_bytes(),self.raw);self.assertEqual(list(self.root.glob('.pdf-render-*')),[])


class DeveloperRendererCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();(self.root/'products/app/frontend').mkdir(parents=True)
        self.source=self.root/'modules/artifactexporter/assets/macos/HTMLPDF.swift';self.source.parent.mkdir(parents=True);self.source.write_text('current worker source')
        self.worker=self.root/'result/native-tools/ResearchHelperHTMLPDF';self.worker.parent.mkdir(parents=True);self.worker.write_bytes(b'current binary')
        self.meta=self.worker.parent/'renderer.json';self.meta.write_text(json.dumps(dict(sha256=hashlib.sha256(self.worker.read_bytes()).hexdigest(),source_sha256=hashlib.sha256(self.source.read_bytes()).hexdigest())))

    def test_only_matching_binary_and_source_can_supply_developer_pdf(self):
        from researchhelper.html_pdf import renderer_path
        self.assertEqual(renderer_path(self.root),self.worker)
        for change in ('binary','source','metadata'):
            with self.subTest(change=change):
                target={'binary':self.worker,'source':self.source,'metadata':self.meta}[change];original=target.read_bytes();target.write_bytes(b'changed')
                with self.assertRaises(ContractError):renderer_path(self.root)
                target.write_bytes(original)

    def candidate(self):
        import zipfile
        archive=self.root/'skill.zip';binary=b'updated worker';manifest={'files':{'app/tools/ResearchHelperHTMLPDF':hashlib.sha256(binary).hexdigest()}}
        with zipfile.ZipFile(archive,'w') as package:
            package.writestr('researchhelper/manifest.json',json.dumps(manifest));package.writestr('researchhelper/app/tools/ResearchHelperHTMLPDF',binary)
        return dict(platform='macos',artifacts=dict(skill=dict(path=str(archive),sha256=hashlib.sha256(archive.read_bytes()).hexdigest())),source_hashes={'modules/artifactexporter/assets/macos/HTMLPDF.swift':hashlib.sha256(self.source.read_bytes()).hexdigest()})

    def test_build_updates_managed_cache_from_the_recorded_skill(self):
        import sys
        sys.path.insert(0,str(ROOT/'packaging'))
        from build_current import cache_native_renderer
        candidate=self.candidate();result=cache_native_renderer(candidate,self.root,self.root/'candidate.json')
        self.assertEqual(result['status'],'updated');self.assertEqual(self.worker.read_bytes(),b'updated worker')
        from researchhelper.html_pdf import renderer_path
        self.assertEqual(renderer_path(self.root),self.worker)
        self.assertEqual(json.loads(self.meta.read_text())['source_sha256'],candidate['source_hashes']['modules/artifactexporter/assets/macos/HTMLPDF.swift'])

    def test_user_changes_and_changed_source_are_preserved(self):
        import sys
        sys.path.insert(0,str(ROOT/'packaging'))
        from build_current import cache_native_renderer
        candidate=self.candidate();self.worker.write_bytes(b'user change')
        self.assertEqual(cache_native_renderer(candidate,self.root,self.root/'candidate.json')['status'],'preserved_modified_cache')
        self.assertEqual(self.worker.read_bytes(),b'user change')
        self.worker.write_bytes(b'current binary');self.source.write_text('new source')
        with self.assertRaises(RuntimeError):cache_native_renderer(candidate,self.root,self.root/'candidate.json')
        self.assertEqual(self.worker.read_bytes(),b'current binary')


import os,shutil,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

@unittest.skipUnless(os.environ.get('RESEARCHHELPER_NATIVE_PDF')=='1','Requires the explicitly built macOS worker')
class NativeHTMLPDFTests(unittest.TestCase):
    def test_detailed_observations_leave_room_for_risk_and_source(self):
        from lxml import html
        from pypdf import PdfReader
        from researchhelper.report_designer import DETAILED_PRINT_STYLE, STYLE, TEMPLATE_SCRIPT

        assets = ROOT / 'modules/visualrenderer/assets'
        scaffold = html.document_fromstring((assets / 'report.html').read_text())
        paper_style = scaffold.xpath('//style[@data-paper-scaffold]')[0].text
        pagination = (assets / 'table-pagination.js').read_text()
        chart_script = TEMPLATE_SCRIPT.replace('__PAPER_FIGURES__', '[]')
        rows = ''.join(
            f'<tr><td>OBSERVATION-{number:02d}</td><td>Original value {number}</td></tr>'
            for number in range(40)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            worker = root / 'app/tools/ResearchHelperHTMLPDF'
            worker.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / 'result/native-tools/ResearchHelperHTMLPDF', worker)
            worker.chmod(0o444)
            source = root / 'report.html'
            source.write_text(f"""<!doctype html><meta charset="utf-8">
<style>{paper_style}{STYLE}{DETAILED_PRINT_STYLE}</style>
<body class="rh-publication" data-report-variant="detailed"><main>
<div class="paper-supporting"><section id="data"><h2>OBSERVATIONS</h2>
<div class="report-data-scroll"><table><thead><tr><th>RECORD</th><th>VALUE</th></tr></thead>
<tbody>{rows}</tbody></table></div></section></div>
<section id="risk" style="min-height:168px"><h2>RISK NOTE</h2><p>Research limitations</p></section>
<footer class="report-source-line">SOURCE NOTE</footer>
</main><script>{pagination}</script><script>{chart_script}</script></body>""")
            artifact = render_saved_html(source, expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                                         resources=root)
            pdf = root / 'report.pdf'
            pdf.write_bytes(artifact)
            pages = PdfReader(pdf).pages
            self.assertEqual(len(pages), 1)
            text = pages[0].extract_text()
            for number in range(40):
                self.assertIn(f'OBSERVATION-{number:02d}', text)
            self.assertIn('RISK NOTE', text)
            self.assertIn('SOURCE NOTE', text)

    def test_long_table_keeps_every_row_and_repeats_headers_in_native_pdf(self):
        from researchhelper.report_designer import STYLE,TEMPLATE_SCRIPT
        from pypdf import PdfReader
        from io import BytesIO
        import re
        pagination=(ROOT/'modules/visualrenderer/assets/table-pagination.js').read_text()
        from lxml import html
        scaffold=html.document_fromstring((ROOT/'modules/visualrenderer/assets/report.html').read_text()).xpath('//style[@data-paper-scaffold]')[0].text
        for preceding_height in (0,520):
            with self.subTest(preceding_height=preceding_height),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary).resolve();worker=root/'app/tools/ResearchHelperHTMLPDF';worker.parent.mkdir(parents=True)
                shutil.copyfile(ROOT/'result/native-tools/ResearchHelperHTMLPDF',worker);worker.chmod(0o444)
                rows=''.join('<tr><td>ROW%03dEND</td><td>%s</td></tr>'%(i,'Long observation '+str(i)) for i in range(95))
                source=root/'report.html'
                source.write_text('<!doctype html><meta charset="utf-8"><style>'+scaffold+STYLE+'</style><body class="rh-publication" data-report-variant="detailed"><main><div style="height:'+str(preceding_height)+'px"></div><table><thead><tr><th>RECORDCODE</th><th>OBSERVATION</th></tr></thead><tbody>'+rows+'</tbody></table></main><script>'+pagination+'</script><script>'+TEMPLATE_SCRIPT.replace('__PAPER_FIGURES__','[]')+'</script></body>')
                frozen=source.read_bytes();data=render_saved_html(source,expected_sha256=hashlib.sha256(frozen).hexdigest(),resources=root)
                pdf=root/'report.pdf';pdf.write_bytes(data)
                from lxml import etree
                bbox=etree.fromstring(subprocess.check_output(['/opt/homebrew/bin/pdftotext','-bbox',str(pdf),'-'],stderr=subprocess.DEVNULL))
                pages=bbox.xpath('//*[local-name()="page"]');seen=[];data_pages=0
                for page in pages:
                    words=sorted(page.xpath('.//*[local-name()="word"]'),key=lambda word:(float(word.get('yMin')),float(word.get('xMin'))))
                    records=[word for word in words if re.fullmatch(r'ROW\d{3}END',word.text or '')]
                    seen.extend(int(word.text[3:6]) for word in records)
                    if records:
                        data_pages+=1
                        for label in ('RECORDCODE','OBSERVATION'):
                            header=next((word for word in words if word.text==label),None)
                            self.assertIsNotNone(header);self.assertLess(float(header.get('yMax')),float(records[0].get('yMin')))
                self.assertGreater(data_pages,1);self.assertEqual(seen,list(range(95)));self.assertLessEqual(len(pages),4)
                self.assertEqual(source.read_bytes(),frozen)

    def test_readonly_installation_and_http_websocket_requests_stay_offline(self):
        requests=[]
        class Canary(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path);self.send_response(200);self.end_headers();self.wfile.write(b'remote must not load')
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Canary);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary).resolve();worker=root/'app/tools/ResearchHelperHTMLPDF';worker.parent.mkdir(parents=True)
                shutil.copyfile(ROOT/'result/native-tools/ResearchHelperHTMLPDF',worker);worker.chmod(0o444);before=worker.stat()
                source=root/'report.html';source.write_text('<!doctype html><meta charset="utf-8"><main>离线报告</main><script>fetch("http://127.0.0.1:'+str(server.server_port)+'/http").catch(()=>{});new WebSocket("ws://127.0.0.1:'+str(server.server_port)+'/websocket");</script>')
                frozen=source.read_bytes();data=render_saved_html(source,expected_sha256=hashlib.sha256(frozen).hexdigest(),resources=root)
                self.assertTrue(data.startswith(b'%PDF-'));self.assertEqual(requests,[])
                self.assertEqual(source.read_bytes(),frozen);self.assertEqual(worker.stat().st_mode,before.st_mode)
                self.assertEqual(list(root.glob('.pdf-render-*')),[])
        finally:server.shutdown();server.server_close();thread.join(timeout=3)
        self.assertFalse(thread.is_alive())

if __name__=='__main__':unittest.main()
