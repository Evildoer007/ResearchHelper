"""Independent acceptance for the new compact, content-addressed local delivery."""
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging'))
sys.path.insert(0, str(ROOT / 'maintenance'))
from build_current import verify_current_build
from candidate_acceptance import verify_mounted_bundle
from tests.test_current_candidate_privacy_offline import allowed


def run():
    selected = os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
    if not selected:
        raise RuntimeError('须指定当次本地候选回执，不按修改时间猜测')
    candidate = verify_current_build(selected, ROOT)
    # Reuse the unchanged private-file rules; the old candidate locator remains
    # historical and is not weakened to accept this different delivery schema.
    with zipfile.ZipFile(candidate['artifacts']['skill']['path']) as package:
        assert package.testzip() is None
        skill_manifest = json.loads(package.read('researchhelper/manifest.json'))
        expected = {'researchhelper/'+name for name in skill_manifest['files']} | {'researchhelper/manifest.json'}
        assert set(package.namelist()) == expected
        for name in package.namelist(): assert allowed(name),name
        for name, value in skill_manifest['files'].items():
            assert hashlib.sha256(package.read('researchhelper/'+name)).hexdigest() == value,name
    for name in candidate['directory_artifacts']['demo']['files']: assert allowed(name),name
    parent = ROOT / 'result/maintenance/oh-current-delivery' / Path(selected).parent.name
    parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='package-', dir=parent))
    record = {'status': 'failed', 'candidate': str(Path(selected).resolve()), 'headless': True,
              'native_window_opened': False, 'installed': False, 'production_store_touched': False,
              'real_data_requests': 0, 'model_calls': 0, 'backend_stopped': False, 'mount_detached': False}
    try:
        with tempfile.TemporaryDirectory(prefix='rh-current-package-', dir=ROOT/'result/acceptance') as folder:
            folder = Path(folder); mount = folder/'disk'; mount.mkdir()
            attached = False
            try:
                subprocess.run(['/usr/bin/hdiutil','attach',candidate['artifacts']['dmg']['path'],
                                '-readonly','-nobrowse','-mountpoint',str(mount)],
                               check=True,capture_output=True,timeout=30)
                attached = True
                bundle = mount/'ResearchHelper.app'
                verify_mounted_bundle(bundle,candidate)
                subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(bundle)],
                               check=True,capture_output=True,timeout=30)
                app_manifest = json.loads((Path(candidate['payload'])/'app-manifest.json').read_text())
                actual = {file.relative_to(bundle).as_posix() for file in bundle.rglob('*') if file.is_file()}
                assert actual == set(app_manifest['files'])
                for name, value in app_manifest['files'].items():
                    file = bundle/name
                    assert allowed(name) and file.resolve().is_relative_to(bundle),name
                    assert hashlib.sha256(file.read_bytes()).hexdigest() == value,name
                resources = bundle/'Contents/Resources'
                licenses = json.loads((resources/'licenses/inventory.json').read_text())
                required = {'tinyshare','pandas','numpy','requests','urllib3','certifi','idna','python-dateutil','six','pytz','tzdata'}
                assert required <= {item['package'] for item in licenses}
                assert len(licenses) == len({item['file'] for item in licenses})
                for item in licenses: assert allowed(item['file']) and (resources/item['file']).stat().st_size>0,item['file']
                record.update(skill_zip_inventory_verified=True,demo_private_files_absent=True,
                              app_complete_inventory_verified=True,vendor_licenses_verified=True,
                              app_private_files_absent=True,app_inventory_files=len(actual))
                files = ['index.html','workspace.js','workspace.css','ui/workspace-controls.js']
                for name in files:
                    assert (bundle/'Contents/Resources/app/web'/name).read_bytes() == (ROOT/'products/app/frontend'/name).read_bytes(),name
                policy = ('(version 1)(allow default)(deny network*)'
                          '(allow network* (local ip "localhost:*"))(allow network* (remote ip "localhost:*"))'
                          '(allow network* (local unix-socket))(allow network* (remote unix-socket))')
                denied = [ROOT/'core',ROOT/'modules',ROOT/'products',Path('/opt/anaconda3'),
                          Path('/Users/haoranxu/Desktop/OptionHelper'),Path('/Users/haoranxu/Desktop/DataFetcher')]
                for directory in denied:
                    policy += '(deny file-read* (subpath '+json.dumps(str(directory))+'))'
                executable = bundle/'Contents/Resources/backend/researchhelper-server/researchhelper-server'
                environment = {'PATH':'/usr/bin:/bin','LANG':'en_US.UTF-8',
                               'RESEARCHHELPER_STORE':str(folder/'store'),'PYTHONDONTWRITEBYTECODE':'1',
                               'PYTHONTZPATH':str(folder/'no-system-tzdata')}
                with (output/'backend.log').open('w') as errors:
                    process = subprocess.Popen(['/usr/bin/sandbox-exec','-p',policy,str(executable)],
                                               cwd=folder,env=environment,stdout=subprocess.PIPE,stderr=errors)
                    try:
                        with selectors.DefaultSelector() as selector:
                            selector.register(process.stdout,selectors.EVENT_READ)
                            if not selector.select(timeout=20): raise RuntimeError('包内后端就绪超时')
                            ready = json.loads(process.stdout.readline())
                        base = ready['url'].rstrip('/')
                        def request(route, cookie=None, body=None, token=None):
                            headers = {'Cookie':cookie} if cookie else {}
                            if body is not None: headers.update({'Content-Type':'application/json','X-ResearchHelper-Token':token})
                            call = urllib.request.Request(base+route,data=json.dumps(body).encode() if body is not None else None,headers=headers)
                            with urllib.request.urlopen(call,timeout=5) as response:
                                return response.read(),response.headers
                        auth = json.loads(request('/api/auth/status')[0])
                        _, headers = request('/api/auth/login',body={'account':'admin','password':'66666666','remember':False},token=auth['token'])
                        cookie = headers.get('Set-Cookie').split(';')[0]
                        bootstrap = json.loads(request('/api/bootstrap',cookie)[0])
                        assert bootstrap['offline'] is True and not bootstrap['tasks']
                        assert len(bootstrap['history']) == 6
                        for name in files:
                            route = '/' if name == 'index.html' else '/'+name
                            assert hashlib.sha256(request(route,cookie)[0]).digest() == hashlib.sha256((ROOT/'products/app/frontend'/name).read_bytes()).digest(),name
                        assert b'echarts' in request('/reports/semi.html',cookie)[0]
                        record.update(signature_verified=True,readonly_image_verified=True,
                                      packaged_frontend_files=files,public_reports=6,
                                      development_and_shared_products_denied=True,anaconda_denied=True)
                    finally:
                        process.terminate()
                        try: process.wait(timeout=5)
                        except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)
                        process.stdout.close();record['backend_stopped']=process.poll() is not None
            finally:
                if attached:
                    subprocess.run(['/usr/bin/hdiutil','detach',str(mount)],check=True,capture_output=True,timeout=30)
                    record['mount_detached']=True
        assert verify_current_build(selected,ROOT) == candidate
        record['status']='scoped_passed'
    except Exception as error:
        record['error']=str(error)
        raise
    finally:
        (output/'receipt.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'status':record['status'],'receipt':str(output/'receipt.json')},ensure_ascii=False))


if __name__ == '__main__':
    run()
