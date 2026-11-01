"""Verify this review's failure and keyboard cases against exact DMG resources."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

from tests.accept_desk_evidence_package import (ROOT,verify_current_build,write_record,
                                              recorded_manifest,verify_bundle,signature)


def run(candidate_path=None):
    selected=Path(candidate_path or os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve()
    candidate=verify_current_build(selected,ROOT)
    output=Path(tempfile.mkdtemp(prefix='ui-review15-package-',dir=ROOT/'result/acceptance'))
    mount=output/'mounted';mount.mkdir()
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
        paths=[name for name in manifest['files'] if name.endswith('/workspace.js')]
        if len(paths)!=1:raise RuntimeError('Ambiguous packaged workspace')
        frontend=(bundle/paths[0]).parent
        env={**os.environ,'RESEARCHHELPER_UI_FRONTEND':str(frontend),
             'RESEARCHHELPER_UI_OUTPUT':str(output/'browser'),'RESEARCHHELPER_UI_CAPTURE':'after'}
        result=subprocess.run(['/usr/local/bin/node',str(ROOT/'tests/accept_desk_review15_quality.mjs')],
                              cwd=ROOT,env=env,text=True,capture_output=True,timeout=120)
        (output/'browser.log').write_text(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError('Packaged review failed; see browser.log')
        browser=json.loads((output/'browser/after/receipt.json').read_text())
        if browser['status']!='passed' or browser['errors'] or browser['external_requests']:
            raise RuntimeError('Incomplete isolated UI receipt')
        if verify_current_build(selected,ROOT)!=candidate:raise RuntimeError('Candidate changed')
        record.update(status='scoped_passed',checks=len(browser['checks']),
                      browser=str(output/'browser/after/receipt.json'),readonly_image_verified=True,
                      signature_verified=True,complete_inventory_verified=True)
    except BaseException as error:
        record.update(status='failed',error=str(error));raise
    finally:
        if attached:
            try:
                subprocess.run(['/usr/bin/hdiutil','detach',str(mount)],check=True,capture_output=True,timeout=30)
                record['mount_detached']=True
            except BaseException as error:
                record.update(status='failed',detach_error=str(error));write_record(output/'receipt.json',record);raise
        write_record(output/'receipt.json',record)
        print(json.dumps(dict(status=record['status'],checks=record.get('checks',0),
                             receipt=str(output/'receipt.json')),ensure_ascii=False))
    return record


if __name__=='__main__':run()
