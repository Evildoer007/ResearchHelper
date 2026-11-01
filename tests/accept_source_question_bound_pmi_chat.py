"""One fresh ordinary Chat from source before making another release package.

This is explicitly not installed-App acceptance. It uses the unchanged Chat
collector, a private Store and the RH-only saved services. No plan, data or
answer is supplied; neither old tasks nor production settings are reopened.
"""
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys

from tests import ROOT
from tests.accept_one_sentence_research import (PRODUCTION, AppClient, OneSentenceRuntime,
                                               run as collect)
from tests.accept_installed_manufacturing_pmi_chat import QUESTION
from researchhelper.model import ModelSettings

CASE = 'source-question-bound-priority-pmi-chat-20261017-v1'
PYTHON = Path('/opt/anaconda3/envs/MachineLearning/bin/python')


def source_inventory():
    sys.path.insert(0, str(ROOT / 'packaging'))
    from source_snapshot import source_files
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in source_files(ROOT)}


class SourceResearchRuntime(OneSentenceRuntime):
    def start(self):
        self.inventory = source_inventory()
        self.record.update(candidate=None, backend_mode='development_source',
            source_manifest=self.inventory, installed=False, formal_app_launcher_used=False,
            installed_application_modified=False, bundled_backend_isolated=False,
            external_network_allowed=True, source_research_quality_accepted=False)
        policy = '(version 1)(allow default)'
        for target in (PRODUCTION.parent, Path('/Users/haoranxu/Desktop/OptionHelper'),
                       Path('/Users/haoranxu/Desktop/DataFetcher')):
            policy += '(deny file-read* file-write* (subpath ' + json.dumps(str(target)) + '))'
        policy += '(deny file-write* (subpath "/Applications/ResearchHelper.app"))'
        environment = dict(PATH='/usr/bin:/bin', LANG='en_US.UTF-8', PYTHONDONTWRITEBYTECODE='1',
                           RESEARCHHELPER_STORE=str(self.output / 'store'))
        self.process = subprocess.Popen(['/usr/bin/sandbox-exec', '-p', policy, str(PYTHON), '-B',
            str(ROOT / 'products/app/backend/entry.py')], cwd=self.output, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=20):
                raise RuntimeError('source_backend_not_ready')
            base = json.loads(self.process.stdout.readline())['url']
        if not base.startswith('http://127.0.0.1:'):
            raise RuntimeError('unexpected_source_backend_origin')
        return AppClient(base.rstrip('/') + '/')

    def stop(self):
        super().stop()
        self.record['source_unchanged_during_study'] = self.inventory == source_inventory()
        if not self.record['source_unchanged_during_study']:
            raise RuntimeError('research_source_changed_during_study')


def run():
    controls = dict(extraction_mode=ModelSettings(PRODUCTION).read().get('extraction_mode', 'default'))
    return collect(case=CASE, candidate_path=ROOT, runtime_class=SourceResearchRuntime,
                   question=QUESTION, model_overrides=controls)
