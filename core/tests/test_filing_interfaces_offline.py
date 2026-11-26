"""App/Skill filing parity with explicit synthetic review, never real approval."""
import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError
from tests import test_autonomous_supplement_interfaces_offline as supplement
from tests import test_filing_workflow_offline as filing
from researchhelper.evidence import FILING_REQUEST_REGISTRY
from tests.skill_fixture_support import build_skill_fixture, source_file

ROOT = Path(__file__).resolve().parents[2]


def semantics(task):
    return {"status": task["status"],
            "answers": [{key: q.get(key) for key in ("question", "status", "answer", "refs", "calculation_refs")}
                        for q in task["questions"]],
            "evidence": [{key: e.get(key) for key in ("id", "object", "text", "period", "nature", "source_check", "applicability_check")}
                         for e in task["evidence"]],
            "model_calls": task["ledger"]["used"]["model_calls"],
            "data_requests": task["ledger"]["used"]["data_requests"]}


class FilingInterfacesTests(unittest.TestCase):
    # Reuse the loopback harness without collecting its TestCase again.
    setUp = supplement.SupplementInterfacesTests.setUp
    stop = supplement.SupplementInterfacesTests.stop
    request = supplement.SupplementInterfacesTests.request
    login = supplement.SupplementInterfacesTests.login

    def test_authenticated_app_and_isolated_skill_deliver_same_filing_fact(self):
        self.shared_filing_case(filing.RESPONSE)

    def test_signed_directory_never_exports_authorization_in_app_or_skill(self):
        response = copy.deepcopy(filing.RESPONSE)
        response['tables'][0]['pdfURL'] = ('http://ft.10jqka.com.cn/standardgwapi/api/news_service/pubnote/download/resource'
            '?seq=10001&token=fabricated-private-signature&userid=fixture-user&ip=192.0.2.1')
        self.shared_filing_case(response)

    def shared_filing_case(self, response):
        bootstrap = self.request('/api/bootstrap')[0]
        self.assertEqual(bootstrap['filing_operations'], FILING_REQUEST_REGISTRY)
        self.assertEqual(bootstrap['filing_operations']['ifind.report_query']['permission'], '未验收')
        options = dict(question='600000.SH公告中的政策何时生效？', entry='个股', objects=['600000.SH'],
            cutoff='2026-10-02T18:00:00+08:00', questions=[dict(id='q1', question='600000.SH公告中的政策何时生效？', kind='fact')])
        task = self.request('/api/tasks', options)[0];route = '/api/tasks/' + task['id']
        task = self.request(route + '/snapshot', dict(source='ifind', operation='report_query',
            parameters=filing.PARAMETERS, response=response))[0]
        self.assertNotIn('fabricated-private-signature', json.dumps(task))
        task = self.request(route + '/filing', dict(directory=task['source_records'][0]['catalog_entries'][0],
            name='original.pdf', base64=base64.b64encode(filing.policy_pdf()).decode()))[0]
        original = next(row for row in task['source_records'] if row['kind'] == 'filing')
        self.assertEqual(original['stages']['source_check'], '待核验')
        spec = filing.FilingWorkflowTests.spec(None, original)
        # Explicit synthetic labels isolate interface transport and binding.
        # They do not claim the document or a financial conclusion is verified.
        supplement.mock_host_review(self, self.server.agent)
        execution = {key: bootstrap['execution'][key] for key in ('mode', 'policy_version')}
        self.request(route + '/start', dict(expected_execution=execution,
            host_result=dict(evidence_specs=[spec], **filing.FilingWorkflowTests.answer())))
        for worker in self.server.agent.workers.values():
            worker.join(10);self.assertFalse(worker.is_alive())
        final = self.request(route)[0]
        self.assertEqual(final['status'], '已完成', final.get('error') or final['gaps'])
        self.assertTrue(self.server.agent.export(task['id'])['html'])
        self.login('sales')
        with self.assertRaises(HTTPError) as denied:self.request(route)
        self.assertIn(denied.exception.code, (403, 404));denied.exception.close()
        installation = self.root / 'installation';build_skill_fixture(installation)
        for relative in ('app/web/report.css', 'assets/vendor/echarts.min.js'):
            target = installation / relative;target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file(relative), target)
        def hashes():
            return {str(p.relative_to(installation)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in installation.rglob('*') if p.is_file()}
        before = hashes()
        program = r'''
import base64,json,sys
from pathlib import Path
installation,project=map(Path,sys.argv[1:]);sys.path.insert(0,str(installation))
from core.session import Session
import researchhelper
assert Path(researchhelper.__file__).resolve().is_relative_to(installation)
data=json.loads(sys.stdin.read());options=data['options'];question=options.pop('question')
session=Session(project)
try:
 task=session.create(question,**options)
 catalog=session.snapshot(task['id'],'ifind','report_query',data['parameters'],data['response'])
 original=session.filing(task['id'],catalog['catalog_entries'][0],'original.pdf',base64.b64decode(data['original']))
 spec=data['spec'];spec['artifact_id']=original['id']
 spec['publication']['basis_ref'].update(artifact_id=original['id'],snapshot_hash=original['hash'])
 checkpoint=session.run(task['id'],evidence_specs=[spec],**data['answers'],defer_delivery=True)
 request=session.argument_review_inputs(task['id'])['request']
 related=sorted({qid for target in request['targets'] for qid in target['question_ids']})
 wire=dict(version=request['version'],review_hash=request['review_hash'],questions=[
  dict(question_id=qid,status='answered',reason='合成协议检查，不是金融质量批准',missing_evidence=[]) for qid in related],
  reviews=[dict(target_id=t['id'],segments=[dict(text=t['text'],verdict='supported',
   basis_refs=['evidence:'+t['evidence_refs'][0]],explanation='固定合成原件，只验证复核协议和绑定')]) for t in request['targets']])
 session.review_arguments(task['id'],wire);final=session.deliver(task['id'])
 assert final.get('delivery') and session.export(task['id']).is_file(),(final.get('error'),final['gaps'])
 print(json.dumps(final,ensure_ascii=False))
finally:session.agent.close_runtime()
'''
        packet = dict(options=options, response=response, parameters=filing.PARAMETERS,
            original=base64.b64encode(filing.policy_pdf()).decode(), spec=spec, answers=filing.FilingWorkflowTests.answer())
        run = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(installation), str(self.root / 'project')],
            cwd=self.root, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), input=json.dumps(packet),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr[-2500:])
        self.assertEqual(semantics(final), semantics(json.loads(run.stdout)))
        self.assertEqual(before, hashes())
        for path in (self.root / 'project/data/researchhelper/researchhelper.sqlite3', self.server.agent.archive.path):
            self.assertNotIn(b'fabricated-private-signature', path.read_bytes())
