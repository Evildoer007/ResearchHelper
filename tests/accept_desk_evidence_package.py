"""Run the new evidence interactions against only verified DMG web resources."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'packaging'))
sys.path.insert(0,str(ROOT/'packaging/app/macos'))
from build_current import verify_current_build,write_record
from install_app import recorded_manifest,verify_bundle,signature


def run(candidate_path=None, *, browser_script=None):
    selected=Path(candidate_path or os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve()
    candidate=verify_current_build(selected,ROOT)
    output=Path(tempfile.mkdtemp(prefix='desk-evidence-package-',dir=ROOT/'result/acceptance'))
    mount=output/'mounted';mount.mkdir()
    receipt=output/'receipt.json'
    record=dict(status='running',candidate=str(selected),model_calls=0,data_requests=0,
                foreground_opened=False,production_store_touched=False,mount_detached=False)
    attached=False
    try:
        subprocess.run(['/usr/bin/hdiutil','attach','-readonly','-nobrowse','-mountpoint',str(mount),
                        candidate['artifacts']['dmg']['path']],check=True,capture_output=True,timeout=30)
        attached=True
        if not os.statvfs(mount).f_flag & os.ST_RDONLY:raise RuntimeError('Image is not read-only')
        bundle=mount/'ResearchHelper.app';manifest=recorded_manifest(selected)
        verify_bundle(bundle,manifest);signature(bundle)
        entries=[name for name in manifest['files'] if name.endswith('/workspace.js')]
        if len(entries)!=1:raise RuntimeError('Ambiguous packaged workspace')
        frontend=(bundle/entries[0]).parent
        env={**os.environ,'RESEARCHHELPER_UI_FRONTEND':str(frontend),'RESEARCHHELPER_UI_OUTPUT':str(output/'browser')}
        script=Path(browser_script or ROOT/'tests/accept_desk_evidence_ui.mjs').resolve()
        if not script.is_file() or not script.is_relative_to(ROOT/'tests') or script.suffix!='.mjs':
            raise ValueError('Browser acceptance script must belong to this test suite')
        record['browser_script']=str(script)
        result=subprocess.run(['/usr/local/bin/node',str(script)],
                              cwd=ROOT,env=env,text=True,capture_output=True,timeout=120)
        (output/'browser.log').write_text(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError('Packaged evidence UI failed; see browser.log')
        browser=json.loads((output/'browser/receipt.json').read_text())
        if (browser['status']!='passed' or browser['errors'] or browser['external_requests']
                or not browser['browser_closed']):raise RuntimeError('Incomplete isolated UI receipt')
        if verify_current_build(selected,ROOT)!=candidate:raise RuntimeError('Candidate changed during acceptance')
        record.update(status='scoped_passed',checks=len(browser['checks']),browser=str(output/'browser/receipt.json'),
                      readonly_image_verified=True,signature_verified=True,complete_inventory_verified=True)
    except BaseException as error:
        record.update(status='failed',error=str(error));raise
    finally:
        if attached:
            try:
                subprocess.run(['/usr/bin/hdiutil','detach',str(mount)],check=True,capture_output=True,timeout=30)
                record['mount_detached']=True
            except BaseException as error:
                record.update(status='failed',detach_error=str(error));write_record(receipt,record);raise
        write_record(receipt,record)
        print(json.dumps(dict(status=record['status'],checks=record.get('checks',0),receipt=str(receipt)),ensure_ascii=False))
    return record


if __name__=='__main__':run()
