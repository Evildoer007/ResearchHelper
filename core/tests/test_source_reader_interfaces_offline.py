"""Authenticated App/isolated Skill parity for lossless, bounded reading."""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError

from tests import test_autonomous_supplement_interfaces_offline as interfaces
from tests import test_autonomous_reasoning_offline as fixtures
from tests import test_source_reader_offline as readers

from tests.skill_fixture_support import build_skill_fixture, source_file

ROOT = Path(__file__).resolve().parents[2]


def semantics(task):
    return {"status":task["status"],"answer":task["questions"][0].get("answer"),
            "text":[e["text"] for e in task["evidence"]],
            "model_calls":task["ledger"]["used"]["model_calls"],
            "data_requests":task["ledger"]["used"]["data_requests"]}


class SourceReaderInterfaceTests(unittest.TestCase):
    setUp = interfaces.SupplementInterfacesTests.setUp
    stop = interfaces.SupplementInterfacesTests.stop
    request = interfaces.SupplementInterfacesTests.request
    login = interfaces.SupplementInterfacesTests.login

    def task(self):
        options={"question":"这份政策什么时候生效？","objects":["材料示例政策"],"cutoff":fixtures.CUTOFF,
                 "questions":[{"id":"q1","question":"这份政策什么时候生效？","kind":"fact"}]}
        task=self.request('/api/tasks',options)[0]
        task=self.request('/api/tasks/'+task['id']+'/material',{'name':'long-policy.txt','base64':base64.b64encode(readers.BODY.encode()).decode()})[0]
        return task,options

    def test_read_endpoints_obey_ownership_binding_and_do_not_approve_or_spend(self):
        task,_=self.task();route='/api/tasks/'+task['id']
        directory=self.request(route+'/source-catalog',{})[0]
        selection=readers.SourceReaderTests.selection(task)
        bundle=self.request(route+'/read-materials',{'requests':selection,'binding':directory['binding']})[0]
        saved=self.request(route)[0]
        self.assertEqual(saved['status'],'待开始')
        self.assertFalse(saved['evidence'])
        self.assertEqual(saved['ledger'],task['ledger'])
        self.assertEqual(len(saved['source_readings']),1)
        self.assertEqual(bundle,self.request(route+'/read-materials',{'requests':selection,'binding':directory['binding']})[0])
        self.assertEqual(len(self.request(route)[0]['source_readings']),1)
        bad=dict(directory['binding'],task_id='rh_foreign')
        with self.assertRaises(HTTPError) as error:
            self.request(route+'/read-materials',{'requests':selection,'binding':bad})
        self.assertEqual(error.exception.code,400);error.exception.close()
        self.login('sales')
        for action,payload in [('source-catalog',{}),('read-materials',{'requests':selection,'binding':directory['binding']})]:
            with self.assertRaises(HTTPError) as error:
                self.request(route+'/'+action,payload)
            self.assertIn(error.exception.code,(403,404));error.exception.close()
        self.assertEqual(len(self.server.agent.archive.get(task['id'])['source_readings']),1)

    def test_authenticated_app_and_independent_skill_deliver_same_read_fact_without_second_model(self):
        task,options=self.task();route='/api/tasks/'+task['id']
        directory=self.request(route+'/source-catalog',{})[0]
        bundle=self.request(route+'/read-materials',{'requests':readers.SourceReaderTests.selection(task),'binding':directory['binding']})[0]
        execution=self.request('/api/bootstrap')[0]['execution']
        payload={'evidence_specs':[fixtures.AutonomousReasoningTests.policy_spec(task)],
                 'reading_hash':bundle['reading_hash'],**fixtures.AutonomousReasoningTests.answer()}
        self.request(route+'/start',{'expected_execution':{k:execution[k] for k in ('mode','policy_version')},'host_result':payload})
        for worker in self.server.agent.workers.values():
            worker.join(10);self.assertFalse(worker.is_alive())
        final=self.request(route)[0]
        self.assertEqual(final['status'],'已完成',final.get('error'))
        self.assertTrue(self.server.agent.export(task['id'])['content']['reading_coverage'])
        prior_revision=final['revision']
        self.request(route+'/read-materials',{'requests':readers.SourceReaderTests.selection(final),'binding':directory['binding']})
        self.assertEqual(self.request(route)[0]['revision'],prior_revision,'Frozen delivery must not change on reading')

        installation=self.root/'installation'
        build_skill_fixture(installation)

        for relative in ('app/web/report.css','assets/vendor/echarts.min.js'):
            target=installation/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source_file(relative),target)
        def hashes():
            return {str(p.relative_to(installation)):hashlib.sha256(p.read_bytes()).hexdigest() for p in installation.rglob('*') if p.is_file()}
        before=hashes()
        script='''import importlib.util,json,sys
from pathlib import Path
from unittest.mock import patch
installation,project,repository=map(Path,sys.argv[1:]);sys.path.insert(0,str(installation));sys.path.append(str(repository))
spec=importlib.util.spec_from_file_location("isolated_session",installation/"core/session.py")
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
import researchhelper
assert Path(researchhelper.__file__).resolve().is_relative_to(installation)
from tests import test_autonomous_reasoning_offline as fixtures
from tests import test_source_reader_offline as readers
from tests.test_source_reader_interfaces_offline import semantics
options=json.loads(sys.stdin.read());question=options.pop("question")
with patch("socket.socket.connect",side_effect=AssertionError("No network")),patch("researchhelper.model.ModelService.reason",side_effect=AssertionError("No second model")):
 session=module.Session(project);task=session.create(question,**options)
 session.material(task["id"],"long-policy.txt",readers.BODY.encode());task=session.archive.get(task["id"])
 directory=session.sources(task["id"]);bundle=session.read(task["id"],readers.SourceReaderTests.selection(task),directory["binding"])
 final=session.run(task["id"],evidence_specs=[fixtures.AutonomousReasoningTests.policy_spec(task)],reading_hash=bundle["reading_hash"],**fixtures.AutonomousReasoningTests.answer())
 assert final["delivery"] and session.export(task["id"]).is_file(),final.get("error")
 assert session.agent.model is None
 print(json.dumps(semantics(final),ensure_ascii=False))
'''
        environment=dict(os.environ,PYTHONPATH=str(installation),PYTHONDONTWRITEBYTECODE='1')
        run=subprocess.run([sys.executable,'-c',script,str(installation),str(self.root/'project'),str(ROOT)],cwd=self.root,env=environment,
                           input=json.dumps(options),capture_output=True,text=True,timeout=30)
        self.assertEqual(run.returncode,0,run.stderr+run.stdout)
        self.assertEqual(semantics(final),json.loads(run.stdout))
        self.assertEqual(before,hashes())

    def test_host_explicit_read_binding_cannot_reference_an_unread_fact(self):
        task,_=self.task();route='/api/tasks/'+task['id']
        directory=self.request(route+'/source-catalog',{})[0]
        selected=readers.SourceReaderTests.selection(task,queries=[],parts=[{'part':'段落1','start':0,'end':6}])
        bundle=self.request(route+'/read-materials',{'requests':selected,'binding':directory['binding']})[0]
        execution=self.request('/api/bootstrap')[0]['execution']
        with self.assertRaises(HTTPError) as error:
            self.request(route+'/start',{'expected_execution':{k:execution[k] for k in ('mode','policy_version')},'host_result':{
                'reading_hash':bundle['reading_hash'],'evidence_specs':[fixtures.AutonomousReasoningTests.policy_spec(task)]}})
        self.assertEqual(error.exception.code,400);error.exception.close()
        final=self.request(route)[0]
        self.assertEqual(final['status'],'待开始')
        self.assertEqual(final['ledger'],task['ledger'])
        self.assertFalse(final['evidence'])


if __name__=='__main__':
    unittest.main()
