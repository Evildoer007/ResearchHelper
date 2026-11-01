"""Build into a fresh candidate folder; keep installed and prior release copies intact."""
import json
from pathlib import Path
import subprocess
import tempfile

from tests.review_report_reader_release import BASE, ROOT, digest, production_files, read, source_inventory

PYTHON = '/opt/anaconda3/envs/MachineLearning/bin/python'


def current_gate():
    candidates = []
    now = source_inventory()
    for path in BASE.glob('source-*/runtime.json'):
        record = read(path)
        if record['status'] == 'passed_with_explicit_assertion_conflicts' and record['source_hashes'] == now:
            candidates.append((path, record))
    assert candidates, 'No complete current-source gate'
    path, record = max(candidates, key=lambda item: item[0].stat().st_mtime_ns)
    assert not record['new_failure_ids'] and not record['errors'] and not record['external_attempts'] and not record['skipped']
    assert record['frozen_live_runs_unchanged'] and record['production_unchanged']
    assert all(digest(path) == value for path, value in record['protected_files'].items())
    return path, record


def build():
    gate_path, gate = current_gate()
    parent = ROOT/'dist/candidates'
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='report-reader-', dir=parent))
    out = Path(tempfile.mkdtemp(prefix='build-', dir=BASE))
    production = production_files()
    installed_skill = {str(path): digest(path) for path in (ROOT/'.skills/researchhelper').rglob('*')
                       if path.is_file() and '__pycache__' not in path.parts}
    prior_release = {str(path): digest(path) for path in (ROOT/'dist/0.10.0').rglob('*')
                     if path.is_file() and path.name in ('release-manifest.json', 'app-manifest.json',
                         'ResearchHelper-0.10.0-arm64.dmg', 'ResearchHelper-Skill-0.10.0.zip', 'ResearchHelper.icns')}
    program = '''import importlib.util,json,sys
from pathlib import Path
root=Path.cwd()
spec=importlib.util.spec_from_file_location('researchhelper_staged_builder',root/'packaging/assemble_release.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
builder.OUTPUT=Path(sys.argv[1])
print(json.dumps(builder.build(install_skill=False)),flush=True)
'''
    record = {'status': 'failed', 'stage': str(stage), 'source_gate': str(gate_path),
              'source_gate_sha256': digest(gate_path), 'source_hashes': gate['source_hashes'],
              'installed_skill_before': installed_skill, 'production_before': production,
              'prior_release_before': prior_release, 'log': str(out/'build.log'),
              'installed_app_updated': False, 'demo_synced': False,
              'real_model_calls': 0, 'real_supplier_calls': 0, 'full_harness_complete': False}
    try:
        with (out/'build.log').open('x') as log:
            subprocess.run([PYTHON,'-u','-B','-c',program,str(stage)],cwd=ROOT,
                           stdout=log,stderr=log,check=True)
        assert source_inventory() == gate['source_hashes'], 'Source changed while building'
        assert production == production_files(), 'Production archive changed'
        assert all(digest(path) == value for path, value in installed_skill.items()), 'Installed Skill changed'
        assert all(digest(path) == value for path, value in prior_release.items()), 'Prior release changed'
        release = read(stage/'release-manifest.json')
        assert all(digest(ROOT/name) == value for name, value in release['source_hashes'].items())
        assert all(digest(item['path']) == item['sha256'] for item in release['artifacts'].values())
        record.update(status='built_not_accepted',artifacts=release['artifacts'],
                      installed_skill_unchanged=True,production_unchanged=True,prior_release_unchanged=True)
    except Exception as error:
        record['error'] = str(error)
        raise
    finally:
        (out/'receipt.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'status':record['status'],'stage':str(stage),'receipt':str(out/'receipt.json'),
                          'error':record.get('error')}),flush=True)
    return record


if __name__ == '__main__':
    build()
