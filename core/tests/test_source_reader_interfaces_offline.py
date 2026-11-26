"""Authenticated App/isolated Skill parity for lossless, bounded reading."""
import base64
import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import unittest
import tempfile
from unittest.mock import patch
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


def policy_date_review(request, expected_answer):
    """A test host may support this one literal date, never arbitrary claims."""
    assert len(request['targets']) == 1
    target = request['targets'][0]
    assert target['text'] == expected_answer and target['question_ids'] == ['q1']
    assert target['evidence_refs'] == ['effective_date'] and not target['calculation_refs']
    return dict(version=request['version'], review_hash=request['review_hash'],
        questions=[dict(question_id='q1',status='answered',missing_evidence=[],
            reason='固定原件明确列示生效日，仅核对条款文字，不推断投资结果。')],
        reviews=[dict(target_id=target['id'],segments=[dict(text=expected_answer,verdict='supported',
            basis_refs=['evidence:effective_date'],explanation='固定合成原件的生效条款逐字支持该回答。')])])


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
        from researchhelper import argument_support
        task,options=self.task();route='/api/tasks/'+task['id']
        answers=fixtures.AutonomousReasoningTests.answer()
        expected=answers['answers']['q1']['answer']
        original_prepare=self.server.agent._prepare_argument_review
        def prepare(current,proposal,selected):
            if argument_support.enabled(current) and argument_support.find_record(current,proposal) is None:
                self.assertEqual([e['text'] for e in current['evidence']],['政策自2026年7月21日起生效。'])
                request=argument_support.review_request(current,proposal)
                wire=policy_date_review(request,expected)
                result=argument_support.decode_review(wire,argument_support.review_schema(request),request)
                current.setdefault('argument_support_reviews',[]).append(
                    argument_support.make_record(current,proposal,result,actor='host'))
            return original_prepare(current,proposal,selected)
        reviewer=patch.object(self.server.agent,'_prepare_argument_review',side_effect=prepare)
        reviewer.start();self.addCleanup(reviewer.stop)
        directory=self.request(route+'/source-catalog',{})[0]
        selection=readers.SourceReaderTests.selection(task,queries=['发布日期','生效'])
        bundle=self.request(route+'/read-materials',dict(requests=selection,binding=directory['binding']))[0]
        execution=self.request('/api/bootstrap')[0]['execution']
        specification=fixtures.AutonomousReasoningTests.policy_spec(task)
        payload=dict(evidence_specs=[specification],reading_hash=bundle['reading_hash'],**answers)
        self.request(route+'/start',dict(expected_execution={k:execution[k] for k in ('mode','policy_version')},host_result=payload))
        for worker in self.server.agent.workers.values():
            worker.join(10);self.assertFalse(worker.is_alive())
        final=self.request(route)[0]
        self.assertEqual(final['status'],'已完成',{k:final.get(k) for k in ('error','gaps','rejected_evidence')})
        self.assertEqual(len(final['argument_support_reviews']),1)
        frozen=self.server.agent.export(task['id'])
        self.assertTrue(frozen['content']['reading_coverage'])
        revision=final['revision']
        self.request(route+'/read-materials',dict(requests=selection,binding=directory['binding']))
        self.assertEqual(self.request(route)[0]['revision'],revision)
        self.assertEqual(self.server.agent.export(task['id']),frozen)
        with tempfile.TemporaryDirectory(prefix='rh-reader-skill-') as name:
            work=Path(name).resolve();installation=work/'installation';project=work/'project'
            build_skill_fixture(installation)
            def hashes():
                return {str(p.relative_to(installation)):hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in installation.rglob('*') if p.is_file()}
            before=hashes()
            packet=dict(options=options,body=readers.BODY,specification=specification,answers=answers)
            program='\n'.join([
                'import copy,importlib.util,json,sys',
                'from pathlib import Path',
                'installation,project=map(lambda p:Path(p).resolve(),sys.argv[1:]);sys.path.insert(0,str(installation))',
                "spec=importlib.util.spec_from_file_location('reader_skill',installation/'core/session.py')",
                'module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)',
                'import researchhelper.engine',
                'assert Path(researchhelper.engine.__file__).resolve().is_relative_to(installation)',
                inspect.getsource(policy_date_review),inspect.getsource(semantics),
                "packet=json.loads(sys.stdin.read());options=packet['options'];question=options.pop('question')",
                'session=module.Session(project)',
                'try:',
                ' task=session.create(question,**options)',
                " original=session.material(task['id'],'long-policy.txt',packet['body'].encode())",
                " directory=session.sources(task['id'])",
                " selection=[dict(artifact_id=original['id'],snapshot_hash=original['snapshot_hash'],queries=['发布日期','生效'],parts=[],reason='核对发布和生效条款',question_ids=['q1'])]",
                " bundle=session.read(task['id'],selection,directory['binding'])",
                " entry=packet['specification'];entry['artifact_id']=original['id']",
                " entry['publication']['basis_ref'].update(artifact_id=original['id'],snapshot_hash=original['snapshot_hash'])",
                " staged=session.run(task['id'],evidence_specs=[entry],reading_hash=bundle['reading_hash'],defer_delivery=True,**packet['answers'])",
                " assert staged['status']=='待开始' and not staged.get('delivery'),staged.get('gaps')",
                " assert len(staged['evidence'])==1 and staged['evidence'][0]['text']==entry['text']",
                " request=session.argument_review_inputs(task['id'])['request']",
                " session.review_arguments(task['id'],policy_date_review(request,packet['answers']['answers']['q1']['answer']))",
                " final=session.deliver(task['id']);assert final['status']=='已完成',final.get('gaps')",
                " assert session.agent.model is None and len(final['argument_support_reviews'])==1",
                " frozen=copy.deepcopy(session.agent.export(task['id']))",
                " assert frozen['content']['reading_coverage'] and session.export(task['id']).is_file()",
                " revision=final['revision'];session.read(task['id'],selection,directory['binding'])",
                " assert session.status(task['id'])['revision']==revision and session.agent.export(task['id'])==frozen",
                ' print(json.dumps(semantics(final),ensure_ascii=False))',
                'finally:session.agent.close_runtime()'])
            script=work/'check.py';script.write_text(program)
            policy='(version 1)(allow default)(deny network*)'
            for protected in (ROOT,ROOT.parent/'OptionHelper',ROOT.parent/'DataFetcher',Path.home()/'Library/Application Support/ResearchHelper'):
                policy+='(deny file-read* file-write* (subpath '+json.dumps(str(protected))+'))'
            policy+='(deny file-write* (subpath '+json.dumps(str(installation))+'))'
            process=subprocess.run(['/usr/bin/sandbox-exec','-p',policy,sys.executable,'-I','-B',str(script),str(installation),str(project)],
                input=json.dumps(packet,ensure_ascii=False),cwd=work,capture_output=True,text=True,timeout=45)
            self.assertEqual(process.returncode,0,process.stderr[-2500:])
            self.assertEqual(json.loads(process.stdout),semantics(final))
            self.assertEqual(hashes(),before)


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
