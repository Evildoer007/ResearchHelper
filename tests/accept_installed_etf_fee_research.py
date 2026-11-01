"""One ordinary ETF comparison using the actual installed App, in a private Store.

No prepared plan, material, answer, new model profile, SDK login or foreground
window. The existing collector retains the finite ledger and clears only its
own temporary credentials. Source and production stores are denied at runtime.
"""
import json
from pathlib import Path
import selectors
import subprocess
import sys

from tests.accept_one_sentence_research import ROOT, PRODUCTION, AppClient, OneSentenceRuntime, run as collect

CANDIDATE = ROOT/'result/build-candidates/20261016-113006-746530/candidate.json'
CASE = 'installed-etf-management-custody-comparison-20261016-v1'
QUESTION = ('比较510300.SH和510500.SH在2026年半年度报告中披露的年度管理费率和年度托管费率，'
    '分别列出两项合计及两基金之间的差值，生成带图表、来源与限制的研究报告。'
    '这是按原件披露口径进行的费用比较，不把费率差当作实际持有收益差，也不作买卖建议。')
INSTALLATION = Path('/Applications/ResearchHelper.app')


class InstalledETFResearchRuntime(OneSentenceRuntime):
    def start(self):
        sys.path[:0] = [str(ROOT/'packaging'), str(ROOT/'packaging/app/macos')]
        from build_current import verify_current_build
        from install_app import recorded_manifest, verify_bundle, signature
        verify_current_build(CANDIDATE, ROOT)
        verify_bundle(INSTALLATION, recorded_manifest(CANDIDATE))
        signature(INSTALLATION)
        policy = '(version 1)(allow default)'
        for target in [ROOT/name for name in ('core','modules','products')]+[
                Path('/opt/anaconda3'), PRODUCTION.parent,
                Path('/Users/haoranxu/Desktop/OptionHelper'), Path('/Users/haoranxu/Desktop/DataFetcher')]:
            policy += '(deny file-read* file-write* (subpath '+json.dumps(str(target))+'))'
        policy += '(deny file-write* (subpath '+json.dumps(str(INSTALLATION))+'))'
        executable = INSTALLATION/'Contents/Resources/backend/researchhelper-server/researchhelper-server'
        environment = dict(PATH='/usr/bin:/bin', LANG='en_US.UTF-8', PYTHONDONTWRITEBYTECODE='1',
            RESEARCHHELPER_STORE=str(self.output/'store'), PYTHONTZPATH=str(self.output/'no-system-tzdata'))
        self.process = subprocess.Popen(['/usr/bin/sandbox-exec','-p',policy,str(executable)],
            cwd=self.output, env=environment, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=20):
                raise RuntimeError('installed_backend_not_ready')
            base = json.loads(self.process.stdout.readline())['url']
        if not base.startswith('http://127.0.0.1:'):
            raise RuntimeError('unexpected_installed_backend_origin')
        self.record.update(backend_mode='actual_installed_bundle', signature_verified=True,
            bundled_backend_isolated=True, external_network_allowed=True, mounted_image_used=False,
            formal_app_launcher_used=False, installed_application_modified=False)
        return AppClient(base.rstrip('/')+'/')


def run():
    sys.path[:0] = [str(ROOT/'packaging'), str(ROOT/'packaging/app/macos')]
    from build_current import verify_current_build
    from install_app import recorded_manifest, verify_bundle, signature
    # Fail before consuming the one-shot study guard if the selected install
    # or delivery is no longer the recorded current version.
    verify_current_build(CANDIDATE, ROOT)
    verify_bundle(INSTALLATION, recorded_manifest(CANDIDATE))
    signature(INSTALLATION)
    return collect(case=CASE, candidate_path=CANDIDATE,
                   runtime_class=InstalledETFResearchRuntime, question=QUESTION)


if __name__ == '__main__':
    run()
