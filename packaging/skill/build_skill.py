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
from release_contract import VERSION, OUTPUT, MODULES
from shared_payload import hashes, copy_tree, copy_runtime, runtime_identity, copy_browser_notices, copy_report_design

def skill(*, install=False, output=None, pdf_renderer=None):
    global OUTPUT
    OUTPUT = Path(output) if output is not None else OUTPUT
    if not isinstance(install, bool):
        raise ValueError('Skill installation must be explicit')
    target = OUTPUT / "researchhelper"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'SKILL.md', target / 'SKILL.md')
    shutil.copyfile(ROOT / 'core/requirements.lock', target / 'requirements.lock')
    shutil.copyfile(ROOT / 'packaging/skill/SKILL_README.md', target / 'README.md')
    copy_tree(ROOT / 'references', target / 'references')
    copy_tree(ROOT / 'agents', target / 'agents')
    visual_guide = target / 'docs' / 'VISUAL_LIBRARY.md'
    visual_guide.parent.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / 'docs' / 'VISUAL_LIBRARY.md', visual_guide)
    for module in MODULES:
        guide = target / 'modules' / module / 'module.md'
        guide.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / 'modules' / module / 'module.md', guide)
    # Developer implementation pointers resolve to the self-contained engine
    # and report resources in the installed Skill, not the developer tree.
    for directory in ('references', 'modules'):
        for guide in (target / directory).rglob('*.md'):
            text = re.sub(r'modules/[^/]+/src/', 'researchhelper/',
                             guide.read_text().replace('core/src/researchhelper/', 'researchhelper/')
                             .replace('products/app/frontend', 'app/web').replace('modules/visualrenderer/assets/', 'app/web/report-design/'))
            for module in MODULES:
                text = text.replace('modules/' + module + '/src', 'researchhelper')
            guide.write_text(text)
    (target / 'core').mkdir(exist_ok=True)
    shutil.copyfile(ROOT / 'core/session.py', target / 'core/session.py')
    copy_runtime(target / 'researchhelper')
    (target / "app" / "web").mkdir(parents=True, exist_ok=True)
    for name in ('report.css','publication.css','draft-figures.js','visual-catalog-data.js','visual-library.js'):
        shutil.copyfile(ROOT / 'products/app/frontend'/name, target/'app/web'/name)
    copy_report_design(target / 'app/web', root=ROOT)
    from shared_payload import copy_pdf_renderer
    copy_pdf_renderer(target, pdf_renderer, root=ROOT)
    (target / "assets/vendor").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "assets/vendor/echarts.min.js", target / "assets/vendor/echarts.min.js")
    shutil.copyfile(ROOT / "assets/vendor/NOTICE.md", target / "assets/vendor/NOTICE.md")
    notices = copy_browser_notices(target, packages=('Apache ECharts',), root=ROOT)
    (target / 'licenses/inventory.json').write_text(json.dumps(notices, indent=2))
    shutil.copyfile(ROOT / 'products/app/frontend/brand.svg', target / 'assets/brand.svg')
    metadata = target / 'agents/openai.yaml'
    metadata.write_text(metadata.read_text().replace('./products/app/frontend/brand.svg', './assets/brand.svg'))
    manifest = {"version": VERSION, "runtime": runtime_identity(ROOT), "files": hashes(target), "offline_default": True,
                "source_network_policy": "explicit-host-consent", "starts_second_model": False, "project_level": True}
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    archive = OUTPUT / f"ResearchHelper-Skill-{VERSION}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                bundle.write(path, "researchhelper/" + str(path.relative_to(target)))
    if not install:
        return archive
    from skill.install_project_skill import install_project_skill
    install_project_skill(archive.resolve(), project_root=ROOT,
                          expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    return archive
