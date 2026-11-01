"""Exercise the actual frozen App and Skill code, offline and headless."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'packaging'), str(ROOT / 'packaging/tests')]
from build_current import verify_current_build
def run_packaged_tests(kind, location, root, output, modules, count, *, timeout_seconds=60):
    """Load only the selected delivered engine; retain its actual module paths."""
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
        raise ValueError('Packaged test timeout must be an integer from1 to300 seconds')
    program = '''
import hashlib, importlib.abc, importlib.util, json, marshal, sys, tempfile, unittest
from pathlib import Path
kind, location, root, modules, count, output = sys.argv[1:]
location = Path(location).resolve()
sys.path.insert(0, str(Path(root) / 'tests'))
tempfile.tempdir = output
if kind == 'skill':
 sys.path.insert(0, str(location))
else:
 from PyInstaller.archive.readers import CArchiveReader
 archive = CArchiveReader(str(location))
 pyz = archive.open_embedded_archive(next(name for name, entry in archive.toc.items() if entry[-1] == 'z'))
 class FrozenResearch(importlib.abc.MetaPathFinder, importlib.abc.Loader):
  def find_spec(self, name, path=None, target=None):
   if name == 'researchhelper' or name.startswith('researchhelper.'):
    assert name in pyz.toc, name
    return importlib.util.spec_from_loader(name, self, is_package=bool(pyz.toc[name][0]))
  def create_module(self, spec): return None
  def exec_module(self, module):
   relative = module.__name__.replace('.', '/')
   relative += '/__init__.pyc' if pyz.toc[module.__name__][0] else '.pyc'
   module.__file__ = str(location.parent / '_internal' / relative)
   module.__frozen_origin__ = str(location) + '!/PYZ.pyz/' + module.__name__
   code = pyz.extract(module.__name__)
   module.__frozen_code_sha256__ = hashlib.sha256(marshal.dumps(code)).hexdigest()
   exec(code, module.__dict__)
 sys.meta_path.insert(0, FrozenResearch())
suite = unittest.defaultTestLoader.loadTestsFromNames(json.loads(modules))
result = unittest.TextTestRunner(verbosity=1).run(suite)
assert result.wasSuccessful() and result.testsRun == int(count) and not result.skipped
paths = {name: str(module.__file__) for name, module in sys.modules.items()
 if (name == 'researchhelper' or name.startswith('researchhelper.')) and getattr(module, '__file__', None)}
origins = {name: {'origin': getattr(sys.modules[name], '__frozen_origin__', None),
 'code_sha256': getattr(sys.modules[name], '__frozen_code_sha256__', None)} for name in paths}
assert paths and all(Path(path).resolve().is_relative_to(location) if kind == 'skill'
 else origins[name]['origin'] == str(location) + '!/PYZ.pyz/' + name
 and origins[name]['code_sha256'] for name, path in paths.items()), paths
print(json.dumps({'status': 'passed', 'kind': kind, 'tests_run': result.testsRun,
 'module_paths': paths, 'module_code_origins': origins, 'test_modules': json.loads(modules),
 'real_model_calls': 0, 'real_supplier_calls': 0}))
'''
    profile = '(version 1)(allow default)(deny network*)'
    profile += '(deny file-write* (subpath ' + json.dumps(str(location)) + '))'
    denied = [ROOT / 'core/src', ROOT / 'core/bootstrap.py',
              Path('/Users/haoranxu/Desktop/OptionHelper'), Path('/Users/haoranxu/Desktop/DataFetcher'),
              Path('/Users/haoranxu/Library/Application Support/ResearchHelper')]
    denied += [path / 'src' for path in (ROOT / 'modules').iterdir() if path.is_dir()]
    for path in denied:
        profile += '(deny file-read* (subpath ' + json.dumps(str(path)) + '))'
    result = subprocess.run(['/usr/bin/sandbox-exec', '-p', profile, sys.executable, '-I', '-B', '-c',
        program, kind, str(location), str(root), json.dumps(modules), str(count), str(output)],
        cwd=output, capture_output=True, text=True, timeout=timeout_seconds)
    (output / (kind + '-tests.log')).write_text(result.stdout + result.stderr)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def run():
    selected = Path(os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve()
    candidate = verify_current_build(selected, ROOT)
    output = Path(tempfile.mkdtemp(prefix='delivered-ifind-no-data-', dir=ROOT / 'result/acceptance'))
    record = {'status': 'failed', 'candidate': str(selected), 'tests_per_runtime': 13,
        'scope': 'actual_frozen_and_skill_empty_query_contract', 'real_data_requests': 0,
        'model_calls': 0, 'native_window_opened': False, 'installed': False,
        'production_store_touched': False, 'mount_detached': False, 'temporary_installation_removed': False}
    try:
        with tempfile.TemporaryDirectory(prefix='payload-', dir=output) as folder:
            folder = Path(folder)
            with zipfile.ZipFile(candidate['artifacts']['skill']['path']) as package:
                manifest = json.loads(package.read('researchhelper/manifest.json'))
                names = {'researchhelper/' + name for name in manifest['files']} | {'researchhelper/manifest.json'}
                assert set(package.namelist()) == names and package.testzip() is None
                for info in package.infolist():
                    assert (folder / info.filename).resolve().is_relative_to(folder)
                    assert (info.external_attr >> 16) & 0o170000 != 0o120000
                for name, expected in manifest['files'].items():
                    assert hashlib.sha256(package.read('researchhelper/' + name)).hexdigest() == expected
                package.extractall(folder)
            # The test suite is a top-level fixture module, not tests.__init__:
            # neither runtime can silently import the development bootstrap.
            skill = run_packaged_tests('skill', folder / 'researchhelper', ROOT, output,
                                      ['ifind_no_data_fixture'], 13)
            record['skill'] = skill
            mount = folder / 'mount'
            mount.mkdir()
            attached = False
            try:
                subprocess.run(['/usr/bin/hdiutil', 'attach', candidate['artifacts']['dmg']['path'],
                    '-readonly', '-nobrowse', '-mountpoint', str(mount)], check=True, capture_output=True, timeout=30)
                attached = True
                bundle = mount / 'ResearchHelper.app'
                subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)],
                               check=True, capture_output=True, timeout=30)
                executable = bundle / 'Contents/Resources/backend/researchhelper-server/researchhelper-server'
                record['app'] = run_packaged_tests('app', executable, ROOT, output, ['ifind_no_data_fixture'], 13)
                record['signature_verified'] = True
            finally:
                if attached:
                    subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)], check=True, capture_output=True, timeout=30)
                    record['mount_detached'] = True
        record['temporary_installation_removed'] = True
        assert verify_current_build(selected, ROOT) == candidate
        record['status'] = 'scoped_passed'
    except Exception as error:
        record['error'] = str(error)
        raise
    finally:
        (output / 'receipt.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
        print(json.dumps({'status': record['status'], 'receipt': str(output / 'receipt.json')}, ensure_ascii=False))
    return record


if __name__ == '__main__':
    run()
