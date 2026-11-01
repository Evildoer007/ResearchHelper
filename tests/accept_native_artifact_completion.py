"""Independent hidden Swift/WebKit save acceptance. No supplier/model/network use."""
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import importlib.util,json,subprocess,threading

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'result/acceptance/resdesk-completion-current/native'

class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body=b'<!doctype html><html><body>Isolated save fixture</body></html>'
        self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass

def run():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    spec=importlib.util.spec_from_file_location('rh_native_builder',ROOT/'packaging/app/build_app.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder);builder.OUTPUT=OUTPUT
    executable=OUTPUT/'native-artifact-completion'
    command=['xcrun','swiftc','-parse-as-library','-D','RESEARCHHELPER_NATIVE_UI_TEST',*builder.swift_options(),str(ROOT/'products/app/desktop/macos/ResearchHelperApp.swift'),str(ROOT/'packaging/tests/native_artifact_completion.swift'),'-o',str(executable)]
    result=subprocess.run(command,capture_output=True,text=True,timeout=180)
    if result.returncode:raise RuntimeError(result.stderr[:6000])
    server=ThreadingHTTPServer(('127.0.0.1',0),Page)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        answer=subprocess.run([str(executable),f'http://127.0.0.1:{server.server_port}/',str(OUTPUT)],capture_output=True,text=True,timeout=60)
        if answer.returncode:raise RuntimeError((answer.stdout+'\n'+answer.stderr)[-6000:])
        receipt=json.loads((OUTPUT/'native-save-receipt.json').read_text())
        print(json.dumps({'status':receipt['status'],'checks':len(receipt['checks']),'model_calls':0,'data_requests':0},ensure_ascii=False))
    finally:server.shutdown();server.server_close();worker.join(timeout=2)

if __name__=='__main__':run()
