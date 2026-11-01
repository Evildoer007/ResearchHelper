"""One bounded natural research task using the saved RH model configuration.

No Beta override, fabricated provider response, supplied answer or task retry.
The result is collected for independent review, not self-certified as quality.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

from tests.accept_installed_client_research import InstalledRuntime, store_identity
from tests.accept_current_live_document_reading import PrivateAppClient, eligible_configuration, task_context

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = Path('/Users/haoranxu/Library/Application Support/ResearchHelper')
CASE = 'saved-endpoint-csi300-etf-investment-research-v1'
LIMITS = dict(data_requests=12, model_calls=8, seconds=360)
QUESTION = ('研究沪深300ETF510300.SH在2026年7月13日至10月13日的表现与投资逻辑。'
    '核实价格和基金实际暴露，区分能被证据支持的驱动与未能确认的解释，'
    '结合估值和反证判断是否具备投资吸引力，生成带真实图表、风险和来源的研究报告。')


class AuthorizedInstalledRuntime(InstalledRuntime):
    def start(self):
        original = __import__('subprocess').Popen
        def authorized(arguments, *values, **options):
            if isinstance(arguments, list) and arguments[0] == '/usr/bin/sandbox-exec':
                arguments = list(arguments)
                if arguments[2].count('(deny network*)') != 1:
                    raise RuntimeError('独立验收的网络边界不符，未派发')
                arguments[2] = arguments[2].replace('(deny network*)',
                    '(deny network*)(allow network-outbound (remote tcp "*:443"))'
                    '(allow network-outbound (remote udp "*:53"))')
            return original(arguments, *values, **options)
        with patch('subprocess.Popen', side_effect=authorized):
            return super().start()


def run(refresh_token):
    selected = Path(os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve()
    sys.path[:0] = [str(ROOT / 'packaging'), str(ROOT / 'core')]
    from build_current import verify_current_build, write_record
    from bootstrap import configure_imports
    configure_imports()
    from researchhelper.model import ModelSettings
    from researchhelper.settings import DataSettings
    candidate = verify_current_build(selected, ROOT)
    for path in (ROOT / 'result/acceptance').glob('installed-natural-research-*/receipt.json'):
        prior = json.loads(path.read_text())
        if prior.get('case') == CASE:
            raise RuntimeError('此案例已登记，禁止自动重复派发或重置账本')
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise RuntimeError('本次研究缺少明确授权的iFinD凭据')
    production_before = store_identity()
    original = ModelSettings(PRODUCTION).read(private=True)
    eligible_configuration(original)
    # Keep every saved control, including default effort and endpoint.
    configuration = {name: original[name] for name in
        ('endpoint', 'model', 'api_key', 'extraction_mode', 'reasoning_effort') if name in original}
    output = Path(tempfile.mkdtemp(prefix='installed-natural-research-', dir=ROOT / 'result/acceptance'))
    target = output / 'receipt.json'
    record = dict(status='executing', case=CASE, candidate=str(selected), question=QUESTION,
        limits=LIMITS, saved_model=configuration['model'], saved_endpoint=configuration['endpoint'],
        model_settings_overridden=False, supplier='ifind', user_documents_sent=False,
        supplied_answers=False, supplied_evidence=False, supplied_calculations=False,
        task_retry=False, native_window_opened=False, full_research_accepted=False,
        independent_financial_quality_accepted=False, production_store_touched=False,
        credentials_removed=False, store_identity_before=production_before)
    settings, sources = ModelSettings(output / 'store'), DataSettings(output / 'store')
    runtime = AuthorizedInstalledRuntime(output, record)
    client = task = None

    def observe(current):
        record.update(task_id=current['id'], research_status=current['status'], ledger=current['ledger'],
            evidence_count=len(current['evidence']), calculation_count=len(current['calculations']),
            thesis_count=len(current['theses']), delivery=current['delivery'],
            questions=[{name: item.get(name) for name in ('id', 'question', 'kind', 'required', 'status')}
                for item in current['questions']],
            gaps=current['gaps'], error=current.get('error'))
        write_record(target, record)

    write_record(target, record)
    try:
        settings.save(configuration)
        sources.save({'ifind': {'refresh_token': refresh_token}})
        client = PrivateAppClient(runtime.start())
        bootstrap = client.request('api/bootstrap')
        assert bootstrap['execution']['mode'] == 'offline' and not bootstrap['tasks']
        client.request('api/settings/execution', {'enabled': True, 'confirm': True})
        execution = client.request('api/bootstrap')['execution']
        task = client.request('api/tasks', dict(question=QUESTION, entry='ETF', intent='综合研究',
            objects=['510300.SH'], research_mode='current', limits=LIMITS))
        assert not task['synthetic'] and not task['evidence'] and not task['theses'] and not task['calculations']
        route = 'api/tasks/' + task['id']
        task = client.request(route + '/start', {'expected_task': task_context(task),
            'expected_execution': {name: execution[name] for name in ('mode', 'policy_version')}})
        deadline = time.monotonic() + LIMITS['seconds'] + 10
        while True:
            task = client.request(route)
            observe(task)
            if task['status'] in ('已完成', '部分完成', '失败', '已取消'):
                break
            if time.monotonic() >= deadline:
                client.request(route + '/cancel', {})
                raise RuntimeError('达到独立验收时间边界，已请求取消，不恢复或重派任务')
            time.sleep(.7)
        used = task['ledger']['used']
        assert used['model_calls'] <= LIMITS['model_calls'] and used['data_requests'] <= LIMITS['data_requests']
        assert task['question'] == QUESTION and not task['synthetic']
        assert all(item['source'] == 'ifind' for item in task['source_records'])
        # Keep the actual outcome and complete public records; do not rewrite
        # its status merely because an HTML rendering exists.
        (output / 'task.json').write_text(json.dumps(task, ensure_ascii=False, indent=2))
        if task['delivery']:
            paper = client.request(route + '/report', html=True)
            (output / 'report.html').write_text(paper)
            record['report_sha256'] = hashlib.sha256(paper.encode()).hexdigest()
            assert "data-reader-version='rh-reader-10'" in paper and '<details' not in paper
            record['client_reading_contract'] = True
        record['status'] = 'collected_for_review'
    except Exception as error:
        record.update(status='failed', failure_type=type(error).__name__)
        if client and task:
            try:
                task = client.request('api/tasks/' + task['id'])
                if task['status'] == '运行中':
                    client.request('api/tasks/' + task['id'] + '/cancel', {})
                observe(task)
            except Exception:
                record['task_inspection_failed'] = True
    finally:
        if client:
            try:
                client.request('api/settings/execution', {'enabled': False, 'confirm': True})
            except Exception:
                pass
        runtime.stop()
        settings.save(dict(configuration, api_key='', clear_key=True))
        sources.save({'ifind': {'clear_access_token': True, 'clear_refresh_token': True}})
        record['credentials_removed'] = not list((output / 'store/credentials').glob('*.secret'))
        record['production_store_unchanged'] = store_identity() == production_before
        assert record['credentials_removed'] and record['production_store_unchanged']
        assert verify_current_build(selected, ROOT) == candidate
        write_record(target, record)
    print(json.dumps({name: record.get(name) for name in
        ('status', 'research_status', 'evidence_count', 'calculation_count', 'thesis_count', 'credentials_removed')}
        | {'receipt': str(target)}, ensure_ascii=False))
    return record
