"""One bounded financial-original study in the ordinary installed App.

Uses the user's saved ResearchHelper service settings. No injected research
plan, evidence, answer, lower reasoning mode or cross-product credentials.
"""
import inspect
import json
from pathlib import Path

from tests import accept_default_app_investment_research as collector
from tests.accept_default_app_need_driven_filings import protected_configuration
from tests.accept_default_app_online_research import archive_snapshot

ROOT=Path(__file__).resolve().parents[1]
CANDIDATE=ROOT/'result/build-candidates/20261015-052024-913064/candidate.json'
CASE='default-app-current-financial-original-same-unit-20261015-v1'
QUESTION=('核对600519.SH贵州茅台2026年半年度报告中的营业收入、报表净利润和经营活动产生的现金流量净额。'
    '按研究需要用iFinD定位并下载原件，保留原文金额单位、合并口径及2026年上半年的累计期间，'
    '计算同口径净利润率和经营现金流净利润比，说明两者能与不能说明什么，生成带图表、来源和限制的研究报告。'
    '这是当前版本财务研究，首发时间未知时继续标明未知，不证明历史时点可得或事件因果，不作买卖建议。')


def run():
    import sys
    sys.path[:0]=[str(ROOT/'packaging'),str(ROOT/'packaging/app/macos')]
    from build_current import verify_current_build
    from install_app import recorded_manifest,verify_bundle,signature
    verify_current_build(CANDIDATE,ROOT)
    verify_bundle(Path('/Applications/ResearchHelper.app'),recorded_manifest(CANDIDATE))
    signature(Path('/Applications/ResearchHelper.app'))
    rows,configuration=archive_snapshot(),protected_configuration()
    collector.CASE,collector.QUESTION,collector.CANDIDATE=CASE,QUESTION,CANDIDATE
    collector.LIMITS=dict(data_requests=12,model_calls=8,seconds=600)
    # Adapt only the entry of the independent collector. Do not edit its old
    # ETF test or alter the natural question/plan produced by the App.
    source=inspect.getsource(collector.run)
    for old,new in [("entry='ETF'","entry='个股'"),("objects=['510300.SH']","objects=['600519.SH']")]:
        assert source.count(old)==1
        source=source.replace(old,new)
    namespace=dict(collector.run.__globals__);exec(compile(source,__file__,'exec'),namespace)
    namespace['run']()
    outputs=[path for path in (ROOT/'result/acceptance').glob('default-app-investment-*/receipt.json')
             if json.loads(path.read_text()).get('case')==CASE]
    assert len(outputs)==1
    target=outputs[0];record=json.loads(target.read_text());after=archive_snapshot()
    record.update(scope='current_financial_original_facts_and_registered_ratios',
        prior_archive_rows_unchanged=all(value<=after.get(table,set()) for table,value in rows.items()),
        saved_configuration_unchanged=protected_configuration()==configuration,
        real_financial_study_accepted=False,full_investment_research_accepted=False)
    task_path=target.with_name('task.json')
    if task_path.is_file():
        task=json.loads(task_path.read_text())
        record['financial_methods']=[item['method'] for item in task['calculations']]
        record['real_financial_study_accepted']=(task['status']=='已完成' and bool(task['delivery'])
            and {'net_profit_margin_v1','operating_cash_profit_ratio_v1'}.issubset(record['financial_methods']))
    assert record['prior_archive_rows_unchanged'] and record['saved_configuration_unchanged']
    target.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print(json.dumps(dict(status=record['status'],research_status=record.get('research_status'),
        real_financial_study_accepted=record['real_financial_study_accepted'],receipt=str(target)),ensure_ascii=False))
    return record


if __name__=='__main__':run()
