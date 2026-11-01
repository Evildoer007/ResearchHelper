"""One new ordinary App study after PDF cell extraction was actually delivered.

No closed-task retry, supplied facts, injected plan or model-mode override.
"""
from pathlib import Path
from tests import accept_default_app_financial_research as financial

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT/'result/build-candidates/20261015-064732-707112/candidate.json'
CASE = 'default-app-current-financial-table-geometry-20261015-v1'
QUESTION = (
    '核对600519.SH贵州茅台2026年半年度的营业收入、报表净利润和经营活动产生的现金流量净额。'
    '优先使用iFinD已登记数据，需要原文解释或核对时按需取得适用资料。'
    '保留原始金额单位、合并口径和上半年累计期间，计算同口径净利润率和经营现金流净利润比，'
    '说明两者能与不能说明什么，生成带图表、来源和限制的完整研究报告。'
    '采用当前版本，首发时间未知时明确标明，不证明历史时点可得或事件因果，不作买卖建议。')


def run():
    financial.CANDIDATE, financial.CASE, financial.QUESTION = CANDIDATE, CASE, QUESTION
    return financial.run()


def run_deadline_revision():
    financial.CANDIDATE = ROOT/'result/build-candidates/20261015-065642-646440/candidate.json'
    financial.CASE = 'default-app-current-financial-table-deadline-20261015-v2'
    financial.QUESTION = QUESTION
    return financial.run()


if __name__ == '__main__':
    run()
