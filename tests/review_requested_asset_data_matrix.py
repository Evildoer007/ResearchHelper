"""Read-only requested-field review, not a provider-success scoreboard."""
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path

from tests import ROOT
from tests.test_asset_data_acceptance_review import load_retained_task, verify_source
from tests.test_current_requested_data_review import FIELDS, PERIOD
from tests.probe_multitype_acquisition_current import inspect_series
from researchhelper.contracts import validate_evidence

MATRIX=ROOT/'result/acceptance/requested-asset-data-matrix-r_5e_wvb'
TOPICS=ROOT/'result/acceptance/requested-asset-topics-xj6ju423'
BROKER_LABELS=dict(company_revenue='营业收入',parent_net_profit='归属于母公司股东的净利润',
    brokerage='经纪业务',asset_management='资产管理业务',securities_investment='证券投资业务',
    underwriting='证券承销业务',other_business='其他业务')


def broker_fields(task):
    code, fields=FIELDS['broker_financials']
    return {name:[e['id'] for e in task['evidence']
        if type(e.get('value')) in (int,float) and Decimal(str(e['value']))==Decimal(value)
        and e.get('unit')==unit and e.get('object')==code and e.get('period')==PERIOD
        and e.get('source_check')==e.get('applicability_check')=='通过'
        and BROKER_LABELS[name] in e.get('text','')]
        for name,(value,unit) in fields.items()}


def read_explicit(folder):
    receipt=json.loads((folder/'explicit-receipt.json').read_text())
    path=folder/'explicit-task.json.gz'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==receipt['retained_task_sha256']
    with gzip.open(path,'rt',encoding='utf-8') as stream:task=json.load(stream)
    assert receipt['owned_runtime_closed'] and receipt['production_unchanged'] and receipt['source_unchanged']
    assert task['ledger']['used']['model_calls']==0
    for source in task['source_records']:verify_source(source,task)
    for row in receipt['results']:
        if row['status']!='returned':continue
        source=next(s for s in task['source_records'] if s['id']==row['artifact_id'])
        assert row['snapshot_hash']==source['hash']
        if source['operation'] in ('cmd_history_quotation','date_sequence'):
            assert inspect_series(source,source['parameters'])==row['inspection']
    return task,receipt


def review():
    record=json.loads((MATRIX/'receipt.json').read_text())
    assert record['status']=='closed_pending_independent_required_field_review'
    output=dict(scope='independent_data_only_review',network_calls=0,model_calls=0,
        all_asset_types_accepted=False,installed_app_accepted=False,report_accepted=False,
        financial_approval=False,original_receipts_rewritten=False,task_resumed=False,
        explicit_samples=[],autonomous_samples=[])
    for folder in (MATRIX,TOPICS):
        _,receipt=read_explicit(folder)
        for row in receipt['results']:
            inspection=row.get('inspection',{})
            contract=inspection.get('contract',{})
            output['explicit_samples'].append(dict(label=row['label'],status=row['status'],
                raw_series_complete=inspection.get('usable'),
                observed_members=contract.get('observed_rows'),
                candidate_count=contract.get('returned_unique_codes'),
                parsed_parts=inspection.get('readable_parts'),
                complete_universe_approved=False,unit_approved=False,
                diagnostic_input_missing_topic=row['status']=='failed' and not row.get('failures'),
                receipt=str(folder/'explicit-receipt.json')))
    for case in record['autonomous_cases']:
        path=Path(case['receipt']);receipt=json.loads(path.read_text());task=load_retained_task(path.parent)
        assert receipt['owned_runtime_closed'] and receipt['production_unchanged'] and receipt['source_unchanged']
        assert not any(receipt[k] for k in ('supplied_codes','supplied_plan','supplied_evidence','supplied_answers','report_accepted'))
        assert not any(e.get('research_phase') in ('analyze','review_support','write') for e in task['ledger']['dispatches'])
        for source in task['source_records']:verify_source(source,task)
        for evidence in task['evidence']:validate_evidence(evidence,task)
        row=dict(name=case['name'],question=case['question'],receipt=str(path),
            receipt_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            evidence_count=len(task['evidence']),used=task['ledger']['used'],
            driver_status=receipt['status'],gaps=task['gaps'],required_data_complete=False)
        if case['name']=='broker':
            row['required_fields']=broker_fields(task)
            row['required_data_complete']=all(row['required_fields'].values()) and not task['gaps']
            row['scope']='指定公司的七项半年报金额，不代表全部公司或财务字段'
            row['incidental_percent_evidence']=[e['id'] for e in task['evidence'] if e.get('unit')=='%']
        elif case['name']=='index_weights':
            row['scope']='沪深300完整名单与逐只权重尚未接入正式工具，不以指数行情替代'
        else:
            row['scope']='行业分类、完整名单及逐公司字段覆盖尚未核验，不以检索候选数替代'
        output['autonomous_samples'].append(row)
    return output


def run():
    output=review();target=MATRIX/'independent-review.json'
    if target.exists():raise ValueError('不覆盖已有独立复核')
    target.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(receipt=str(target),cases=[dict(name=r['name'],complete=r['required_data_complete'])
        for r in output['autonomous_samples']]),ensure_ascii=False),flush=True)


if __name__=='__main__':run()
