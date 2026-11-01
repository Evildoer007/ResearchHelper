"""Actual WebKit/AppKit printing of frozen candidate HTML, without panels."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.accept_native_workbench_render import RenderRuntime
from tests.accept_current_live_document_reading import PrivateAppClient

NODE = Path('/usr/local/bin/node')
MARKER = Path('/Users/haoranxu/.codex/plugins/cache/openai-primary-runtime/pdf/26.1017.22136/skills/pdf/container_tools/mark_artifact_operation_started.mjs')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pdf_text_key(value):
    # Apple's Chinese font ToUnicode maps some glyphs to equivalent radicals.
    # Normalize extraction only; never rewrite the PDF, source text or numbers.
    value = unicodedata.normalize('NFKC', value)
    value = value.translate(str.maketrans({'⻅': '见', '⻓': '长', '⻛': '风'}))
    return ''.join(value.split())


def run():
    source_mode = os.environ.get('RESEARCHHELPER_PRINT_SOURCE_ONLY') == '1'
    selected = Path(os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve() if not source_mode else None
    sys.path.insert(0, str(ROOT / 'packaging'))
    from build_current import verify_current_build
    candidate = verify_current_build(selected, ROOT) if selected else None
    output = Path(tempfile.mkdtemp(prefix='native-hidden-print-', dir=ROOT / 'result/acceptance'))
    record = dict(status='failed', candidate=str(selected) if selected else None, model_calls=0, provider_calls=0,
        foreground_opened=False, production_store_touched=False, physical_job_sent=False,
        interactive_print_panel_accepted=False, compiled_native_controller_not_installed_binary=True)
    runtime = RenderRuntime(output, record)
    server = worker = None
    try:
        previous_selected = os.environ.pop('RESEARCHHELPER_CURRENT_RECEIPT', None) if source_mode else None
        try:
            base = runtime.start()
        finally:
            if previous_selected is not None:
                os.environ['RESEARCHHELPER_CURRENT_RECEIPT'] = previous_selected
        client = PrivateAppClient(base)
        assert client.request('api/bootstrap')['execution']['mode'] == 'offline'
        paper = client.request('reports/semi.html', html=True)
        raw = paper.encode()
        record['actual_candidate_report_sha256'] = hashlib.sha256(raw).hexdigest()
        (output / 'candidate-report.html').write_bytes(raw)

        class FrozenReport(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != '/reports/semi.html':
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *values):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), FrozenReport)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        spec = importlib.util.spec_from_file_location('print_native_builder', ROOT / 'packaging/app/build_app.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        files = [ROOT / 'products/app/desktop/macos/ResearchHelperApp.swift',
            ROOT / 'packaging/tests/native_hidden_print.swift']
        record['native_sources'] = {str(file.relative_to(ROOT)): digest(file) for file in files}
        with tempfile.TemporaryDirectory(prefix='compile-', dir=output) as directory:
            builder.OUTPUT = Path(directory)
            binary = Path(directory) / 'native-hidden-print'
            result = subprocess.run(['xcrun', 'swiftc', '-parse-as-library', '-DRESEARCHHELPER_NATIVE_UI_TEST',
                *builder.swift_options(), *(str(file) for file in files), '-framework', 'AppKit',
                '-framework', 'WebKit', '-o', str(binary)], capture_output=True, text=True, timeout=120)
            (output / 'compiler.log').write_text(result.stdout + result.stderr)
            assert result.returncode == 0, 'Native helper compilation failed'
            # Required PDF artifact bookkeeping, exactly once before authoring.
            reused = os.environ.get('RESEARCHHELPER_PDF_MARKER_REUSE')
            if reused:
                previous = Path(reused).resolve()
                assert previous.is_relative_to(ROOT / 'result/acceptance') and previous.name == 'receipt.json'
                prior = json.loads(previous.read_text())
                assert prior['status'] == 'failed' and prior['native']['print_operations'] == 0
                assert (previous.parent / 'artifact-operation.log').is_file() and (previous.parent / 'native.log').is_file()
                record['artifact_marker_already_succeeded'] = str(previous)
            else:
                mark = subprocess.run([str(NODE), str(MARKER), '--operation-kind', 'create',
                    '--expected-output-count', '2', '--output-format', 'pdf'],
                    cwd=ROOT, capture_output=True, text=True, timeout=30)
                (output / 'artifact-operation.log').write_text(mark.stdout + mark.stderr)
                assert mark.returncode == 0, 'PDF artifact marker unavailable'
                record['artifact_marker_succeeded'] = True
            result = subprocess.run([str(binary), 'http://127.0.0.1:' + str(server.server_port) + '/', str(output)],
                cwd=output, capture_output=True, text=True, timeout=150)
            (output / 'native.log').write_text(result.stdout + result.stderr)
            record['native'] = json.loads(result.stdout)
            assert result.returncode == 0 and record['native']['status'] == 'passed', record['native'].get('error')
        from pypdf import PdfReader
        texts, pages = {}, {}
        for name in ('closed-reader', 'open-reader'):
            pdf = output / (name + '.pdf')
            reader = PdfReader(pdf)
            texts[name] = ''.join(page.extract_text() or '' for page in reader.pages)
            pages[name] = len(reader.pages)
            assert len(texts[name]) > 500 and 'AI' in texts[name] and 'FY3' in texts[name]
            result = subprocess.run(['pdftoppm', '-r', '120', '-png', str(pdf), str(output / name)],
                capture_output=True, text=True, timeout=60)
            assert result.returncode == 0
        assert pages['closed-reader'] == pages['open-reader']
        assert ''.join(texts['closed-reader'].split()) == ''.join(texts['open-reader'].split())
        printed = pdf_text_key(texts['closed-reader'])
        blocks = json.loads((output / 'client-blocks.json').read_text())
        missing = [text for text in blocks if pdf_text_key(text) not in printed]
        assert not missing, 'Native PDF lost one or more complete client paragraphs'
        assert all(digest(file) == record['native_sources'][str(file.relative_to(ROOT))] for file in files)
        if selected:
            assert verify_current_build(selected, ROOT) == candidate
        record.update(status='pdf_generated_pending_visual_review', pages=pages,
            popup_does_not_change_printed_text=True, actual_saved_report_unchanged=True,
            complete_client_blocks_preserved=len(blocks))
    except Exception as error:
        record['error'] = str(error)
    finally:
        if server:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)
        runtime.stop()
        record['fixture_server_stopped'] = worker is None or not worker.is_alive()
        record['compile_workspace_removed'] = not any(output.glob('compile-*'))
        receipt = output / 'receipt.json'
        receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(dict(status=record['status'], receipt=str(receipt), error=record.get('error')), ensure_ascii=False))
    return record


if __name__ == '__main__':
    raise SystemExit(0 if run()['status'] == 'pdf_generated_pending_visual_review' else 1)
