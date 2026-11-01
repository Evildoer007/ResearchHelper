"""Headless App-interface correction checks on one isolated local service."""
from pathlib import Path
import hashlib,json,os,subprocess,tempfile,threading
from tests import ROOT
from researchhelper.service import make_server

REPORT=dict(title='口径观察',conclusion='比较已有读数，不把同期变化直接解释为因果。',
            logics=[dict(title='区分事实与解释',analysis='保留事实与对应限制。')])


def run():
    output=ROOT/'result/acceptance/report-corrections-20261101';output.mkdir(exist_ok=True)
    record=dict(status='running',model_calls=0,data_requests=0,native_window_opened=False,
                production_store_touched=False,synthetic_fixture=True)
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        server=make_server(temporary,ROOT)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            tasks=[]
            for name in ('更正流程验收','独立对照任务'):
                task=server.agent.create(name);task['synthetic']=True;server.agent.archive.save(task)
                tasks.append(server.agent.start(task['id'],dict(data_requests=[],draft_report=REPORT)))
            before={t['id']:server.agent.archive.report(t['id'],1)['digest'] for t in tasks}
            server.agent.archive.correction(tasks[0]['id'],1,'经营现金流与归母净利润的归属范围不同。')
            server.agent.archive.correction(tasks[0]['id'],1,'年度利润不是当期TTM分母，不能据此识别价格涨幅来源。')
            fixture=output/'fixture.json';fixture.write_text(json.dumps(dict(ids=[t['id'] for t in tasks]),indent=2))
            env=dict(os.environ,RESEARCHHELPER_UI_BASE='http://127.0.0.1:'+str(server.server_port),
                     RESEARCHHELPER_UI_OUTPUT=str(output),RESEARCHHELPER_UI_FIXTURE=str(fixture))
            with (output/'browser.log').open('w') as stream:
                done=subprocess.run(['/usr/local/bin/node',str(ROOT/'tests/accept_report_corrections.mjs')],cwd=ROOT,
                    env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=120)
            if done.returncode:raise RuntimeError('Report correction browser check failed; see browser.log')
            for task in tasks:
                assert server.agent.archive.report(task['id'],1)['digest']==before[task['id']]
                assert server.agent.archive.get(task['id'])['ledger']==task['ledger']
            assert len(server.agent.archive.correction_records(tasks[0]['id'],1))==3
            assert not server.agent.archive.correction_records(tasks[1]['id'],1)
            record.update(status='passed',browser_receipt=str(output/'browser.json'),reports_unchanged=True,
                          ledgers_unchanged=True,notes_only_on_selected_task=True)
        except BaseException as error:record.update(status='failed',error=str(error));raise
        finally:
            server.shutdown();server.server_close();thread.join(timeout=10)
            record['service_stopped']=not thread.is_alive()
            (output/'receipt.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(status=record['status'],receipt=str(output/'receipt.json'))),flush=True)
    return record


if __name__=='__main__':run()
