"""One new ordinary ETF study; preserve the App's actual model phase controls."""
from unittest.mock import patch
from tests import ROOT
from tests import accept_installed_etf_fee_research as installed
from tests.accept_one_sentence_research import PRODUCTION, run as collect
from tests.accept_installed_client_research import (
    INSTALLATION, recorded_manifest, signature, verify_bundle, verify_current_build)
from researchhelper.model import ModelSettings


CANDIDATE = ROOT / 'result/build-candidates/20261017-082719-433694/candidate.json'
CASE = 'installed-natural-huaxia300-june-nav-chat-20261017-v1'
QUESTION = ('研究510330.SH在2026年6月30日的收盘价与该日官方单位净值，'
            '计算当日收盘折溢价并说明口径，自行取得数据，生成带图表和来源的研究报告。')


def run():
    verify_current_build(CANDIDATE, ROOT)
    verify_bundle(INSTALLATION, recorded_manifest(CANDIDATE))
    signature(INSTALLATION)
    controls = dict(extraction_mode=ModelSettings(PRODUCTION).read().get('extraction_mode', 'default'))
    with patch.object(installed, 'CANDIDATE', CANDIDATE):
        return collect(case=CASE, candidate_path=CANDIDATE,
            runtime_class=installed.InstalledETFResearchRuntime,
            question=QUESTION, model_overrides=controls)


if __name__ == '__main__':
    run()
