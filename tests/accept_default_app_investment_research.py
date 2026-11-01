"""Collect one full ETF investment study from the installed default App Store.

Unlike price-only acceptance, valuation, exposure, competing explanations and
investment implications remain in scope. Missing support is not a pass. This
collector neither injects a plan nor retries a task or changes configuration.
"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests.accept_default_app_online_research import DefaultAppRuntime, STORE, CANDIDATE
from tests.accept_current_live_document_reading import task_context

CASE='default-app-full-etf-investment-after-source-repair-20261014-v1'
QUESTION=('研究沪深300ETF510300.SH在2026年7月13日至10月13日的表现与投资逻辑。'
    '核实价格和基金实际暴露，区分能被证据支持的驱动与未能确认的解释，'
    '结合估值和反证判断是否具备投资吸引力，生成带真实图表、风险和来源的研究报告。')
LIMITS=dict(data_requests=100,model_calls=8,seconds=600)


def run():
    for path in (ROOT/'result/acceptance').glob('default-app-investment-*/receipt.json'):
        if json.loads(path.read_text()).get('case')==CASE:
            raise RuntimeError('此任务已经派发，不重开或重置预算')
    output=Path(tempfile.mkdtemp(prefix='default-app-investment-',dir=ROOT/'result/acceptance'))
    receipt=output/'receipt.json'
    record=dict(status='executing',case=CASE,candidate=str(CANDIDATE),question=QUESTION,
        limits=LIMITS,production_store=str(STORE),native_window_opened=False,
        supplied_answers=False,supplied_evidence=False,supplied_plan=False,task_retry=False,
        model_settings_overridden=False,service_stopped=False,investment_quality_accepted=False)
    runtime,client,task=DefaultAppRuntime(output),None,None

    def persist():
        receipt.write_text(json.dumps(record,ensure_ascii=False,indent=2))

    persist()
    try:
        client=runtime.start()
        boot=client.request('api/bootstrap')
        assert Path(boot['storage_location'])==STORE and boot['execution']['mode']=='live'
        assert boot['research_readiness']['model_configured'] and boot['source_settings']['ifind']['has_refresh_token']
        assert not any(item['status']=='运行中' for item in boot['tasks'])
        task=client.request('api/tasks',dict(question=QUESTION,entry='ETF',intent='综合研究',
            objects=['510300.SH'],research_mode='current',limits=LIMITS))
        record['task_id']=task['id'];persist()
        route='api/tasks/'+task['id']
        client.request(route+'/start',dict(expected_task=task_context(task),
            expected_execution={name:boot['execution'][name] for name in ('mode','policy_version')}))
        deadline=time.monotonic()+LIMITS['seconds']+10
        while True:
            task=client.request(route)
            record.update(research_status=task['status'],ledger=task['ledger'],
                evidence_count=len(task['evidence']),calculation_count=len(task['calculations']),
                thesis_count=len(task['theses']),gaps=task['gaps'])
            persist()
            if task['status'] in ('已完成','部分完成','失败','已取消'):
                break
            if time.monotonic()>deadline:
                client.request(route+'/cancel',{})
                raise RuntimeError('bounded_research_timeout')
            time.sleep(1)
        (output/'task.json').write_text(json.dumps(task,ensure_ascii=False,indent=2))
        if task['delivery']:
            report=client.request(route+'/report',html=True)
            (output/'report.html').write_text(report)
            record['report_sha256']=hashlib.sha256(report.encode()).hexdigest()
        record.update(status='collected_for_independent_review',
            questions=[{key:q.get(key) for key in ('id','question','kind','required','status')} for q in task['questions']])
    except Exception as error:
        record.update(status='failed',failure_type=type(error).__name__)
        if client and task:
            try:
                task=client.request('api/tasks/'+task['id'])
                if task['status']=='运行中':
                    client.request('api/tasks/'+task['id']+'/cancel',{})
                (output/'task.json').write_text(json.dumps(task,ensure_ascii=False,indent=2))
                record.update(research_status=task['status'],ledger=task['ledger'])
            except Exception:
                record['task_inspection_failed']=True
    finally:
        runtime.stop();record['service_stopped']=True;persist()
    print(json.dumps({key:record.get(key) for key in ('status','research_status',
        'evidence_count','calculation_count','thesis_count','failure_type')}
        |{'receipt':str(receipt)},ensure_ascii=False))
    return record


if __name__=='__main__':
    run()
