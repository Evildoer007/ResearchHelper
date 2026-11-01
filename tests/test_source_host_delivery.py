"""Current host reading/checkpoint protocol; reviews are synthetic fixtures."""
import tempfile,unittest
from pathlib import Path
from tests import ROOT
from core.session import Session
from tests import test_autonomous_reasoning_offline as fixtures
from tests import test_source_reader_offline as readers


class SourceHostDeliveryTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='rh-host-reading-')
        self.addCleanup(folder.cleanup);self.project=Path(folder.name)
        self.session=Session(self.project);self.addCleanup(self.session.agent.close_runtime)
        task=self.session.create('这份政策什么时候生效？',objects=['材料示例政策'],cutoff=fixtures.CUTOFF,
            questions=[dict(id='q1',question='这份政策什么时候生效？',kind='fact')])
        self.session.material(task['id'],'long-policy.txt',readers.BODY.encode())
        self.task=self.session.status(task['id']);catalog=self.session.sources(task['id'])
        self.reading=self.session.read(task['id'],readers.SourceReaderTests.selection(self.task),catalog['binding'])
        self.outputs=dict(evidence_specs=[fixtures.AutonomousReasoningTests.policy_spec(self.task)],
            reading_hash=self.reading['reading_hash'],**fixtures.AutonomousReasoningTests.answer())

    def test_read_fact_checkpoint_review_and_delivery_need_no_second_model(self):
        task=self.session.run(self.task['id'],**self.outputs,defer_delivery=True)
        self.assertFalse(task.get('delivery'));self.assertEqual(len(task['evidence']),1)
        description=self.session.argument_review_inputs(task['id']);request=description['request']
        reviews=[]
        for target in request['targets']:
            refs=['evidence:'+ref for ref in target['evidence_refs']]
            self.assertTrue(refs)
            reviews.append(dict(target_id=target['id'],segments=[dict(text=target['text'],verdict='supported',
                basis_refs=refs,explanation='合成流程验收，所引原件条款直接载明生效日期。')]))
        self.session.review_arguments(task['id'],dict(version=request['version'],review_hash=request['review_hash'],
            questions=[dict(question_id='q1',status='answered',reason='合成流程验收，原件生效条款已读取并关联。',missing_evidence=[])],reviews=reviews))
        done=self.session.deliver(task['id'])
        self.assertEqual(done['status'],'已完成',done.get('gaps'))
        self.assertEqual(done['questions'][0]['answer'],self.outputs['answers']['q1']['answer'])
        self.assertEqual(done['ledger']['used']['model_calls'],0);self.assertEqual(done['ledger']['used']['data_requests'],0)
        saved=self.session.archive.report(done['id'],done['version'])
        for profile in ('standard','detailed'):
            file=self.session.export(done['id'],profile=profile)
            self.assertTrue(file.is_file());self.assertIn('2026年7月21日',file.read_text())
        self.session.agent.close_runtime();restarted=Session(self.project)
        try:
            self.assertEqual(restarted.archive.report(done['id'],done['version'])['digest'],saved['digest'])
            self.assertEqual(restarted.status(done['id'])['source_readings'],done['source_readings'])
        finally:restarted.agent.close_runtime()

    def test_unreviewed_fact_delivers_partial_report_without_claiming_full_completion(self):
        done=self.session.run(self.task['id'],**self.outputs)
        self.assertEqual(done['status'],'部分完成');self.assertTrue(done.get('delivery'))
        self.assertTrue(done['evidence']);self.assertFalse(done.get('rejected_evidence'))
        self.assertTrue(any('论证复核' in gap for gap in done['gaps']))
        self.assertTrue(self.session.export(done['id']).is_file())
        self.assertEqual(done['ledger']['used']['model_calls'],0);self.assertEqual(done['ledger']['used']['data_requests'],0)


if __name__=='__main__':unittest.main()
