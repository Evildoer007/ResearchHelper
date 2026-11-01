"""Run explicit test modules against independent Skill or actual frozen code."""
import json
from pathlib import Path
import subprocess

from tests.rebuild_sdk_wire_acceptance import LOCAL_NETWORK_SANDBOX

PYTHON = "/opt/anaconda3/envs/MachineLearning/bin/python"


def run_packaged_tests(kind, location, root, output_directory, test_modules, expected_tests):
    if kind not in ("skill", "app") or not test_modules or expected_tests <= 0:
        raise ValueError("Packaged test scope must be explicit")
    location, root = Path(location).resolve(), Path(root).resolve()
    program = '''
import importlib.abc,importlib.util,json,sys,unittest
from pathlib import Path
kind,location,root,modules,count=sys.argv[1:]
location=Path(location).resolve()
sys.path.insert(0,root)
sys.path.insert(0,str(Path(root)/'tests'))
if kind=='skill':
 sys.path.insert(0,str(location))
else:
 from PyInstaller.archive.readers import CArchiveReader
 archive=CArchiveReader(str(location))
 pyz=archive.open_embedded_archive(next(name for name,entry in archive.toc.items() if entry[-1]=='z'))
 class FrozenResearch(importlib.abc.MetaPathFinder,importlib.abc.Loader):
  def find_spec(self,name,path=None,target=None):
   if name=='researchhelper' or name.startswith('researchhelper.'):
    assert name in pyz.toc,name
    return importlib.util.spec_from_loader(name,self,is_package=bool(pyz.toc[name][0]))
  def create_module(self,spec):return None
  def exec_module(self,module):
   module.__file__=str(location)+'!/PYZ.pyz/'+module.__name__.replace('.','/')+'.pyc'
   exec(pyz.extract(module.__name__),module.__dict__)
 sys.meta_path.insert(0,FrozenResearch())
suite=unittest.defaultTestLoader.loadTestsFromNames(json.loads(modules))
result=unittest.TextTestRunner(verbosity=1).run(suite)
assert result.wasSuccessful() and result.testsRun==int(count) and not result.skipped
paths={name:str(module.__file__) for name,module in sys.modules.items()
 if (name=='researchhelper' or name.startswith('researchhelper.')) and getattr(module,'__file__',None)}
assert paths and all(Path(path).resolve().is_relative_to(location) if kind=='skill'
 else path.startswith(str(location)+'!/PYZ.pyz/') for path in paths.values()),paths
print(json.dumps({'status':'passed','kind':kind,'tests_run':result.testsRun,'module_paths':paths,
 'test_modules':json.loads(modules),'real_model_calls':0,'real_supplier_calls':0}))
'''
    profile = LOCAL_NETWORK_SANDBOX + '(deny file-write* (subpath ' + json.dumps(str(location)) + '))'
    result = subprocess.run(['/usr/bin/sandbox-exec', '-p', profile, PYTHON, '-I', '-B', '-c', program,
        kind, str(location), str(root), json.dumps(test_modules), str(expected_tests)],
        cwd=output_directory, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)
