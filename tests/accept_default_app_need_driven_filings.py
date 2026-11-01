"""One bounded real filing study through the installed App's ordinary API.

No supplied plan, answers, evidence, alternate model or reasoning-setting
override. It is a disclosure-fact case, not the older full investment study.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests import accept_default_app_investment_research as collector
from tests.accept_default_app_online_research import STORE, archive_snapshot

CANDIDATE = ROOT / 'result/build-candidates/20261015-041542-763643/candidate.json'
CASE = 'default-app-need-driven-quarter-original-current-facts-20261015-v1'
QUESTION = ('根据510300.SH截至2026年6月30日的2026年第二季度报告，核对期末资产配置，'
    '包括股票占基金总资产比例、股票投资金额和期末基金份额净值。按研究需要从iFinD定位并下载相关原件，'
    '生成当前版本研究报告，明确披露观测日期和首次公开时间未知的限制。'
    '不判断历史时点可得性、不证明事件因果、不作买入建议。')


def protected_configuration():
    paths = [STORE / name for name in ('model-settings.json', 'source-settings.json', 'execution-policy.json')]
    paths.extend((STORE / 'credentials').glob('*.secret'))
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.is_file()}


def run():
    sys.path[:0] = [str(ROOT / 'packaging'), str(ROOT / 'packaging/app/macos')]
    from build_current import verify_current_build
    from install_app import recorded_manifest, verify_bundle, signature
    verify_current_build(CANDIDATE, ROOT)
    verify_bundle(Path('/Applications/ResearchHelper.app'), recorded_manifest(CANDIDATE))
    signature(Path('/Applications/ResearchHelper.app'))
    rows, configuration = archive_snapshot(), protected_configuration()
    collector.CASE, collector.QUESTION, collector.CANDIDATE = CASE, QUESTION, CANDIDATE
    collector.LIMITS = dict(data_requests=12, model_calls=8, seconds=600)
    result = collector.run()
    outputs = [path for path in (ROOT / 'result/acceptance').glob('default-app-investment-*/receipt.json')
        if json.loads(path.read_text()).get('case') == CASE]
    assert len(outputs) == 1
    target = outputs[0]
    record = json.loads(target.read_text())
    after = archive_snapshot()
    record.update(scope='current_filing_facts_only', full_investment_research_accepted=False,
        prior_archive_rows_unchanged=all(value <= after.get(table, set()) for table, value in rows.items()),
        saved_configuration_unchanged=protected_configuration() == configuration)
    task_path = target.with_name('task.json')
    if task_path.is_file():
        task = json.loads(task_path.read_text())
        record['selected_filings'] = [dict(source_id=r['directory']['source_id'], title=r['directory']['title'],
            selection_reason=r.get('selection_reason'), question_ids=r.get('question_ids'),
            original_sha256=r['snapshot'].get('material', {}).get('hash'),
            current_download_receipt_present=isinstance(r.get('acquisition'), dict))
            for r in task['source_records'] if r.get('kind') == 'filing']
        current = [item for item in task['evidence'] if item.get('availability', {}).get('kind') == 'current_original']
        record['current_original_evidence_count'] = len(current)
        record['unknown_publication_preserved'] = all(item['published_at'] is None for item in current)
        record['explicit_current_use_preserved'] = all(item['applicability_review'].get('temporal_use') == 'current_version' for item in current)
        record['real_filing_case_accepted'] = (task['status'] == '已完成' and len(current) >= 3
            and task['delivery'] is not None and all(q['kind'] == 'fact' for q in task['questions']))
    else:
        record['real_filing_case_accepted'] = False
    assert record['prior_archive_rows_unchanged'] and record['saved_configuration_unchanged']
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(dict(status=record['status'], research_status=record.get('research_status'),
        real_filing_case_accepted=record['real_filing_case_accepted'], receipt=str(target)), ensure_ascii=False))
    return record


if __name__ == '__main__': run()
