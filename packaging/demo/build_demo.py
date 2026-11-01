from __future__ import annotations
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packaging'))
from core.bootstrap import configure_imports
configure_imports()
from release_contract import VERSION, OUTPUT
from shared_payload import hashes, copy_tree, copy_runtime, runtime_identity, copy_browser_notices, copy_report_design

def demo(output=None, *, pdf_renderer=None):
    global OUTPUT
    OUTPUT = Path(output) if output is not None else OUTPUT
    from researchhelper.archive import ResearchArchive
    from researchhelper.engine import ResearchAgent
    from researchhelper.cases import catalog
    from researchhelper.contracts import ENTRIES, INTENTS
    from researchhelper.evidence import active_source_capabilities
    from researchhelper.knowledge import ENTRIES as KNOWLEDGE
    from researchhelper.report_examples import render_example
    target = OUTPUT / "demo"
    copy_tree(ROOT / 'products/app/frontend', target / 'public')
    copy_report_design(target / 'public', root=ROOT)
    copy_tree(ROOT / 'products/app/data/reports', target / 'public/reports')
    copy_runtime(target / 'researchhelper')
    copy_tree(ROOT / 'products/app/frontend', target / 'app/web')
    copy_report_design(target / 'app/web', root=ROOT)
    from shared_payload import copy_pdf_renderer
    copy_pdf_renderer(target, pdf_renderer, root=ROOT)
    copy_tree(ROOT / 'products/app/data', target / 'app/data')
    copy_tree(ROOT / "assets/vendor", target / "assets/vendor")
    notices = copy_browser_notices(target, root=ROOT)
    (target / 'licenses/inventory.json').write_text(json.dumps(notices, indent=2))
    for key in json.loads((ROOT/'products/app/data/report_catalog.json').read_text())['sources']:
        reading = render_example(key, ROOT)
        for directory in ('public/reports', 'app/data/reports'):
            (target/directory/(key+'.html')).write_text(reading, encoding='utf-8')
    (target / "main.py").write_text('"""Generated ResearchHelper demo service, isolated from App production data."""\nfrom pathlib import Path\nimport threading, webbrowser\nfrom researchhelper.service import make_server\n\ndef main():\n    root = Path(__file__).resolve().parent\n    service = make_server(root / "data/researchhelper", root)\n    threading.Timer(.2, lambda: webbrowser.open(f"http://127.0.0.1:{service.server_port}/")).start()\n    service.serve_forever()\n\nif __name__ == "__main__":\n    main()\n')
    with tempfile.TemporaryDirectory(prefix="rh-demo-") as folder:
        agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True)
        tasks = []
        for fixture in ("mechanism", "etf", "value", "index", "sector", "event", "macro", "mixed"):
            task = agent.create_case(fixture)
            task = agent.start(task["id"])
            if task["status"] != "已完成":
                raise RuntimeError(f"Offline fixture failed: {fixture}, {task.get('error')}")
            # A successful freeze no longer carries pending checkpoints.
            # The file demo has no report endpoint, so bundle the actual
            # archived presentation explicitly rather than relying on a
            # temporary field left over from saving.
            report = agent.export(task['id'], task['version'])
            from researchhelper.report_designer import render_html
            tasks.append(dict(task,report_html=report['html'],report_detailed_html=render_html(report['content'],ROOT,profile='detailed')))
        payload = {"version": VERSION, "offline": True, "tasks": tasks, "cases": catalog(), "entries": ENTRIES,
                   "intents": INTENTS, "history": json.loads((ROOT / 'products/app/data/report_catalog.json').read_text())["sources"],
                   "capabilities": active_source_capabilities(), "knowledge": KNOWLEDGE}
        (target / "public/demo-data.js").write_text("window.RH_DEMO = " + json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c") + ";", encoding="utf-8")
    (target / "index.html").write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=public/index.html"><a href="public/index.html">打开ResearchHelper</a>')
    (target / "README.md").write_text("# ResearchHelper demo\n\n0.10.0，由ResearchHelper主项目生成。直接打开index.html可离线阅读、保存草稿与备份。需要运行共享离线引擎时，在已有Python环境运行main.py。它只使用本文件夹data/researchhelper，不连接App生产档案。\n\n合成验收不是实时研究；六份历史报告保留公开内容，机构及内部信息已脱敏。\n")
    (target / "manifest.json").write_text(json.dumps({"version": VERSION, "runtime": runtime_identity(ROOT), "source": "ResearchHelper", "files": hashes(target)}, ensure_ascii=False, indent=2))
    return target
