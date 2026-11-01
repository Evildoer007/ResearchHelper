"""One ordinary macro study through the installed App in a private Store.

No supplied indicator ID, plan, source, evidence or answer. Importing this file
does not make external calls. The existing one-shot collector retains failures,
preserves production settings and clears this study's temporary credentials.
"""
from unittest.mock import patch

from tests import ROOT
from tests import accept_installed_value_recovery_chat as installed


CANDIDATE = ROOT / 'result/build-candidates/20261017-122415-432882/candidate.json'
CASE = 'installed-natural-manufacturing-pmi-chat-20261017-v1'
QUESTION = ('分析国家统计局最新一期制造业PMI，比较生产和新订单表现，'
            '解释供需变化及风险，生成带图表和来源的研究报告。')


def run():
    with patch.object(installed, 'CANDIDATE', CANDIDATE), \
            patch.object(installed, 'CASE', CASE), \
            patch.object(installed, 'QUESTION', QUESTION):
        return installed.run()
