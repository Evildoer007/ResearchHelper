"""Independent numeric replay and explicit defects of the one actual RH run."""
from decimal import Decimal
from pathlib import Path
import hashlib,json,math
from tests import ROOT


def run():
    folder=ROOT/'result/acceptance/live-maotai-harness-current';report=json.loads((folder/'report.json').read_text());draft=report['draft'];facts=draft['facts'];byid={r['id']:r for r in facts};sources={s['id']:s for s in report['source_records']}
    financial=[r for r in facts if r['field'] in ('ths_revenue_stock','ths_np_atoopc_stock','ths_ncf_from_oa_stock')]
    assert len(financial)==6
    for fact in financial:
        source=sources[fact['artifact_id']];locator=fact['locator'];table=source['snapshot']['tables'][locator['table']]
        assert source['parameters']['codes']==fact['code']=='600519.SH'
        raw=table['table'][locator['field']][locator['row']];assert raw==fact['value']==fact['raw_value']
        binding=next(p for p in source['parameters']['indipara'] if p['indicator']==fact['field']);assert binding['indiparams'][0]==fact['period']
    checks=[]
    for calc in draft['calculations']:
        if calc['method']=='raw_change_v1':
            first,last=[Decimal(str(byid[ref]['value'])) for ref in calc['data_refs']];actual=(last/first-1)*100
            assert math.isclose(float(actual),calc['value'],rel_tol=1e-12,abs_tol=1e-12)
            checks.append(dict(label=calc['label'],recomputed=str(actual),reported=calc['value']))
        elif calc['method']=='raw_range_v1':
            close=[r['value'] for r in facts if r['field']=='close'];assert min(close)==calc['inputs']['a'] and max(close)==calc['inputs']['b'];checks.append(dict(label=calc['label'],minimum=min(close),maximum=max(close)))
    probe=json.loads((folder/'valuation-probe.json').read_text());assert probe['errorcode']==0 and not probe['automatic_harness_request']
    usage=[r.get('usage',{}) for r in json.loads((folder/'task.json').read_text())['ledger']['dispatches'] if r['kind']=='model_calls']
    record=dict(status='review_completed',report_generated=True,analysis_quality_passed=False,financial_cells_replayed=6,calculations=checks,source_records=5,financial_observations=6,price_observations=len(facts)-6,price_days=len([r for r in facts if r['field']=='close']),autonomous_valuation_available=any(r['field'] in {'pe','pe_ttm','ths_pe_ttm_stock','ths_pe_index'} for r in facts),independent_pe_probe=probe['tables'][0]['table']['ths_pe_ttm_stock'][0],independent_pe_is_not_part_of_autonomous_report=True,issues=[
        dict(id='requested_metric_omitted',detail='用户要求市盈率，接口已登记且独立请求可返回，但规划器未请求。'),
        dict(id='cashflow_cause_not_collected',detail='经营现金流下降不能直接归因为主营回款或现金转化恶化；公司年报说明了财务子公司的成员单位存款及同业存款因素，自动流程未收集此解释。'),
        dict(id='nonmatching_periods_and_causality',detail='2024至2025年利润变化与2025年10月至2026年10月股价区间不一致，不能称为价格对该盈利变化的反应。'),
        dict(id='unnecessary_price_history',detail='问题要求最新股价，自动规划拉取了一年四列OHLC，产生972条价格观测，增加上下文和报告长度。'),
        dict(id='financial_units_not_auto_confirmed',detail='六个原值与年报元口径一致，但自动字段仍标记原始单位待核对，不能把人工复核冒充引擎已自动核验。')],
        annual_report_reference='https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12104117&stockid=600519',model_calls=len(usage),input_tokens=sum(u.get('input_tokens',0) for u in usage),output_tokens=sum(u.get('output_tokens',0) for u in usage),autonomous_data_requests=5,independent_data_probe_requests=1,no_paid_retries=True)
    (folder/'review.json').write_text(json.dumps(record,ensure_ascii=False,indent=2));print(json.dumps({k:record[k] for k in ('status','analysis_quality_passed','financial_cells_replayed','price_days','autonomous_valuation_available','independent_pe_probe','model_calls','input_tokens','output_tokens')},ensure_ascii=False));return record

if __name__=='__main__':run()
