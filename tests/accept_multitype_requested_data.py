"""Fresh, bounded question-to-data samples, with independent field review.

Only question text and explicit acceptance budgets change in the existing
isolated driver. No plan, response, extraction, credentials or answer is mocked.
This stops before analysis and does not accept reports or installed Apps.
"""
from decimal import Decimal
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from tests import ROOT, accept_autonomous_data_phase as driver
from tests.accept_requested_asset_data_matrix import explicit_checks
from tests.review_requested_asset_data_matrix import broker_fields, read_explicit
from tests.test_asset_data_acceptance_review import verify_source
from tests.test_current_requested_data_review import matching_fields
from researchhelper.contracts import validate_evidence
from researchhelper.data_requirements import pending
from maintenance.compact_acceptance import compact_closed_exports


CASES = (
    ('hk', '获取腾讯控股2026年10月12日至16日每日收盘价，明确代码、实际计价币种及交易日。不做收益计算或投资判断。'),
    ('equity', '核对中信证券2026年半年报营业收入、归属于母公司股东的净利润及五项主要业务营业收入，保留金额、原文单位、完整报告期和原件位置，不做计算、估值或投资判断。'),
    ('etf', '核对沪深300ETF510300.SH和中证500ETF510500.SH在2026年中期报告披露的管理费及托管费年费率，保留各自原值、单位、完整报告期和原件位置。不做费用加总、费率差计算或投资判断。'),
    ('index', '获取沪深300在2026年10月19日的完整成分及各自权重，保留权重日期、单位和来源；缺失的成分或权重不能补零，不做收益或归因分析。'),
    ('industry', '获取证券行业2026年半年报营业收入与归母净利润，用明确的行业分类和完整公司名单核对覆盖情况，不用业务分部收入代替公司收入，不做估值或投资判断。'),
    ('chain_company', '核对电池产业链中宁德时代2026年半年报披露的电池产能、产量和产能利用率，保留数值、原文单位、完整报告期及原件位置；不要把在建产能当作产能，不做计算或投资判断。'),
    ('macro', '核对2026年9月全国居民消费价格同比和环比，保留统计局原文数字、单位、所属月份及位置。不做预测或投资判断。'),
    ('event', '获取2026年9月16日和7月29日两次FOMC声明中的联邦基金目标利率区间原文，保留各自声明日期、原件位置及出处。不将英文分数转换为数字，不做因果分析或投资判断。'),
)


class BoundedDataAgent(driver.DataPhaseAgent):
    def create(self, *args, **kwargs):
        kwargs['limits'] = dict(data_requests=24, model_calls=6, seconds=180)
        return super().create(*args, **kwargs)


def fee_metric_matches(item,metric):
    context=item.get('calculation_context') or {}
    if context.get('metric')==metric and context.get('rate_period')=='annual':return True
    # A fact-only question need not invent a calculation method/context.
    # Independently require the actual literal metric, annual label and basis.
    text=''.join(item.get('text','').split())
    label=('管理人报酬' in text or '管理费' in text) if metric=='management_fee' else '托管费' in text
    return label and '年费率' in text and '基金资产净值' in text


def field_review(name, task):
    """Read benchmarks only after the run; never feed them into the Agent."""
    evidence = task['evidence']
    if name == 'equity':
        fields = broker_fields(task)
    elif name == 'chain_company':
        fields = matching_fields(task, 'battery_operations')
    elif name == 'etf':
        fields = {code + ':' + metric: [e['id'] for e in evidence
            if e.get('object') == code and e.get('unit') == '%'
            and type(e.get('value')) in (int, float)
            and Decimal(str(e['value'])) == Decimal(value)
            and e.get('period') == '2026-01-01/2026-06-30'
            and fee_metric_matches(e,metric)]
            for code in ('510300.SH', '510500.SH')
            for metric, value in (('management_fee', '0.15'), ('custody_fee', '0.05'))}
    elif name == 'macro':
        fields = {label: [e['id'] for e in evidence if e.get('unit') == '%'
            and type(e.get('value')) in (int, float) and Decimal(str(e['value'])) == Decimal(value)
            and e.get('period') == '2026-09' and label in e.get('text', '')]
            for label, value in (('同比', '0.9'), ('环比', '0.2'))}
    elif name == 'hk':
        sources = {s['id']: s for s in task['source_records']}
        fields = {}
        for day in ('2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16'):
            matches = []
            for e in evidence:
                source = sources.get(e.get('artifact_id'), {})
                if source.get('operation') != 'cmd_history_quotation' or e.get('object') != '0700.HK':
                    continue
                table = next((t for t in source['snapshot']['tables'] if t['thscode'] == '0700.HK'), None)
                row = e.get('record_locator', {}).get('row')
                if (table and type(row) is int and 0 <= row < len(table['time'])
                    and table['time'][row] == day
                    and e.get('raw_value') == table['table']['close'][row]
                    and e.get('calculation_context', {}).get('observation_date') == day
                    and e.get('calculation_context', {}).get('currency')):
                    matches.append(e['id'])
            fields[day] = matches
    elif name == 'event':
        fields = {day: [e['id'] for e in evidence
            if e.get('period') == day and 'federal funds' in e.get('text', '').lower()
            and ('range' in e.get('text', '').lower())]
            for day in ('2026-09-16', '2026-07-29')}
    else:
        # A returned file/list is not adopted financial evidence or a verified
        # classification/universe. Neither missing contract is guessed here.
        fields = {'complete_dated_weight_evidence' if name == 'index'
                  else 'verified_classification_universe_and_company_financials': []}
    return fields


def run():
    parent = ROOT / 'result/acceptance'
    parent.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='multitype-requested-data-', dir=parent))
    folder.chmod(0o700)
    target = folder / 'receipt.json'
    result = dict(status='running', scope='fresh_data_only', cases=[],
        analysis_accepted=False, report_accepted=False, installed_app_accepted=False,
        all_asset_types_accepted=False, bounded_probe=dict(data_requests=24, model_calls=6, seconds=180))

    def save():
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    save()
    result['explicit_receipt'] = str(explicit_checks(folder))
    explicit_task, explicit = read_explicit(folder)
    result['explicit_results'] = [dict(label=r['label'], status=r['status'],
        inspection=r.get('inspection')) for r in explicit['results']]
    result['explicit_used'] = explicit_task['ledger']['used']
    save()
    if any(e.get('operation') == 'get_access_token' and e.get('status') != '成功'
           for e in explicit_task['ledger']['dispatches']):
        result['status'] = 'stopped_after_authentication_failure'
        save()
        return target
    for name, question in CASES:
        row = dict(name=name, question=question, fresh=True, status='running')
        result['cases'].append(row)
        save()
        print(json.dumps(dict(case=name, phase='starting'), ensure_ascii=False), flush=True)
        try:
            with patch.object(driver, 'CASE', folder.name + '-' + name), \
                 patch.object(driver, 'QUESTION', question), \
                 patch.object(driver, 'DataPhaseAgent', BoundedDataAgent):
                path = driver.run()
            path = Path(path)
            row['receipt'] = str(path)
            receipt = json.loads(path.read_text())
            task = json.loads((path.parent / 'task.json').read_text())
            assert all(receipt[k] for k in ('production_unchanged', 'source_unchanged', 'owned_runtime_closed'))
            assert not any(receipt[k] for k in ('supplied_plan', 'supplied_evidence', 'supplied_answers', 'report_accepted'))
            assert not any(d.get('research_phase') in ('analyze', 'review_support', 'write')
                           for d in task['ledger']['dispatches'])
            from tests.review_multitype_requested_data import verify_retained_source, broker_literal_review
            for source in task['source_records']:
                verify_retained_source(source, task)
            for item in task['evidence']:
                validate_evidence(item, task)
            fields = field_review(name, task)
            if name == 'equity':
                fields, row['disclosed_precision'] = broker_literal_review(task)
            unmet = pending(task)
            complete = all(fields.values()) and not unmet and not task['gaps'] and not task.get('rejected_evidence')
            row.update(status='requested_sample_complete' if complete else 'requested_sample_incomplete',
                receipt=str(path), driver_status=receipt['status'], fields=fields,
                pending_requirements=unmet, gaps=task['gaps'], error=task.get('error'),
                used=task['ledger']['used'], evidence_count=len(task['evidence']),
                source_operations=[dict(source=s['source'], operation=s['operation'], status=s['status'])
                                   for s in task['source_records']],
                production_unchanged=True, source_unchanged=True, owned_runtime_closed=True)
            del task
            try:
                compact_closed_exports(path.parent)
            except ValueError:
                # A busy export/Store is retained; cleanup is not data failure.
                row['export_compaction'] = 'deferred_without_removal'
        except Exception as error:
            row.update(status='acceptance_failed', error_type=type(error).__name__)
        save()
        print(json.dumps(dict(case=name, status=row['status'], used=row.get('used'), receipt=row.get('receipt')),
                         ensure_ascii=False), flush=True)
    result['status'] = 'closed_with_per_scope_results'
    save()
    print(json.dumps(dict(receipt=str(target), cases=[dict(name=r['name'], status=r['status'])
                                                     for r in result['cases']]), ensure_ascii=False), flush=True)
    return target


if __name__ == '__main__':
    run()
