"""One new packaged study, serialized before temporary credentials or dispatch.

Keep the earlier failed collector unchanged. This adapter rejects an already
mounted candidate and records local startup failures without provider secrets.
It never retries a study, detaches somebody else's image or changes budgets.
"""
from contextlib import contextmanager
from pathlib import Path
import plistlib
import subprocess
import sys

from tests import ROOT
from tests.accept_one_sentence_research import OneSentenceRuntime, PRODUCTION, QUESTION, run as collect
from researchhelper.contracts import ContractError
from researchhelper.model import ModelSettings
from researchhelper.runtime import StoreLease

CASE = 'packaged-research-synthesis-serial-chat-20261020-v1'


def mounted_images():
    response = subprocess.run(['/usr/bin/hdiutil', 'info', '-plist'],
        capture_output=True, check=True, timeout=15)
    return {Path(item['image-path']).resolve()
        for item in plistlib.loads(response.stdout).get('images', []) if item.get('image-path')}


@contextmanager
def image_lease(image, directory):
    image = Path(image).resolve()
    lease = StoreLease(directory)
    try:
        if image in mounted_images():
            raise ContractError('候选镜像仍被其他验收占用，本次尚未创建研究或读取凭据')
        yield
    finally:
        lease.close()


class DiagnosticRuntime(OneSentenceRuntime):
    def start(self):
        try:
            return super().start()
        except subprocess.CalledProcessError as error:
            command = error.cmd if isinstance(error.cmd, list) else []
            program = Path(command[0]).name if command else 'unknown'
            detail = dict(program=program, returncode=error.returncode)
            if program in ('hdiutil', 'codesign'):
                message = error.stderr or b''
                detail['local_diagnostic'] = (message.decode(errors='replace')
                    if isinstance(message, bytes) else message)[:1200]
            self.record['local_startup_failure'] = detail
            raise

    def stop(self):
        try:
            return super().stop()
        except subprocess.CalledProcessError as error:
            try:
                command = error.cmd if isinstance(error.cmd, list) else []
                owned = getattr(self, 'mount', None)
                if (owned is None or command != ['/usr/bin/hdiutil', 'detach', str(owned)]
                        or not self.attached or not self.record.get('service_stopped')
                        or self.process is not None and self.process.poll() is None
                        or not Path(owned).resolve().is_relative_to(self.output.resolve())):
                    raise
                # A terminated service may release its image handle slightly
                # after exit. Never force-detach, close another process or
                # remove a mounted directory. Retry only after no open files.
                opened = subprocess.run(['/usr/sbin/lsof', '+f', '--', str(owned)],
                    capture_output=True, timeout=15)
                if opened.returncode not in (0, 1) or opened.stdout.strip():
                    raise
                subprocess.run(['/usr/bin/hdiutil', 'detach', str(owned)],
                    check=True, capture_output=True, timeout=30)
                self.attached = False
                self.record.update(mount_detached=True, transient_detach_failure_recovered=True)
                if self.mount_folder:
                    self.mount_folder.cleanup()
            except BaseException:
                if self.attached and self.mount_folder:
                    # Do not let TemporaryDirectory recurse into a still-live
                    # read-only mount during interpreter finalization.
                    self.mount_folder._finalizer.detach()
                    self.record['owned_mount_preserved'] = True
                raise


def run(candidate):
    candidate = Path(candidate).resolve()
    sys.path.insert(0, str(ROOT / 'packaging'))
    from build_current import verify_current_build
    release = verify_current_build(candidate, ROOT)
    image = release['artifacts']['dmg']
    lease_directory = ROOT / 'result/acceptance/image-leases' / image['sha256']
    with image_lease(image['path'], lease_directory):
        controls = dict(extraction_mode=ModelSettings(PRODUCTION).read().get('extraction_mode', 'default'))
        return collect(case=CASE, candidate_path=candidate, question=QUESTION,
            model_overrides=controls, runtime_class=DiagnosticRuntime)
