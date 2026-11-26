"""Assemble one current Skill template per test process and copy it per use."""
from functools import cache
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from threading import Lock

ROOT = Path(__file__).resolve().parents[1]
_template_lock = Lock()


@cache
def _skill_template():
    workspace = tempfile.TemporaryDirectory(prefix='rh-skill-test-template-')
    original_path = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location('researchhelper_skill_fixture', ROOT / 'packaging/skill/build_skill.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        builder.skill(output=Path(workspace.name), install=False)
    except BaseException:
        workspace.cleanup()
        raise
    finally:
        sys.path[:] = original_path
    return workspace


def build_skill_fixture(installation):
    installation = Path(installation)
    installation.parent.mkdir(parents=True, exist_ok=True)
    with _template_lock:
        workspace = _skill_template()
    shutil.copytree(Path(workspace.name) / 'researchhelper', installation)
    return installation


def source_file(relative):
    relative=str(relative)
    if relative.startswith('app/web/'):
        return ROOT/'products/app/frontend'/relative.removeprefix('app/web/')
    source = ROOT/relative
    if relative.startswith('core/src/researchhelper/') and not source.is_file():
        name = Path(relative).name
        declared = json.loads((ROOT/'packaging/package-source-map.json').read_text())['files']
        candidates = [ROOT/path for path in declared
            if path.startswith('modules/') and '/src/' in path and Path(path).name == name]
        if len(candidates) != 1:
            raise FileNotFoundError('Runtime source is missing or ambiguous: ' + name)
        return candidates[0]
    return source
