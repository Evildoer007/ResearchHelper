"""Final model/host proposals remain private, durable and version-bound."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json,socket,unittest
from tests import ROOT
from core.session import Session
from researchhelper.archive import decode_body
from researchhelper.contracts import canonical,fingerprint,ContractError

SENTINEL='PRIVATE_SUBMISSION_DIAGNOSTIC_ONLY'
PROPOSAL=dict(title='传导观察',conclusion='区分事实与待验证解释。',
    logics=[dict(title='条件变化',analysis='现有事实仅支持条件性解释。',knowledge_refs=['wrong-shape'])],
    private_debug=SENTINEL)

class DraftSubmissionArchiveTests(unittest.TestCase):
    def setUp(self):
        # Exercise the real Skill boundary: the research project is separate
        # from its read-only installation and is removed after each test.
        self.workspace=TemporaryDirectory(prefix='rh-submission-test-');self.addCleanup(self.workspace.cleanup)
        self.session=Session(Path(self.workspace.name));self.addCleanup(self.session.agent.close_runtime)
        network=patch.object(socket.socket,'connect',side_effect=AssertionError('External calls denied'))
        network.start();self.addCleanup(network.stop)
        self.task=self.session.create('核对资料并解释可能传导，不作产品推荐',entry='事件',objects=['研究对象'],cutoff='2026-01-01')
    def run_report(self,proposal=None):
        return self.session.draft(self.task['id'],report=deepcopy(PROPOSAL if proposal is None else proposal),data_requests=[])
    def test_rejected_reference_shape_survives_without_blocking_the_report(self):
        proposal=deepcopy(PROPOSAL);original=canonical(proposal);task=self.run_report(proposal)
        receipt=task['draft_submissions'][0]
        self.assertEqual(receipt['payload']['logics'][0]['knowledge_refs'],['wrong-shape'])
        self.assertEqual(receipt['payload_hash'],fingerprint(proposal));self.assertEqual(canonical(proposal),original)
        self.assertEqual(receipt['origin'],'host');self.assertEqual(receipt['task_id'],task['id'])
        self.assertEqual(receipt['research_version'],task['version']);self.assertEqual(receipt['batch'],task['batch'])
        self.assertEqual(receipt['input_hash'],task['input_hash']);self.assertEqual(receipt['facts_hash'],fingerprint(task['draft_report']['facts']))
        self.assertTrue(task['delivery']);self.assertEqual(task['draft_report']['logics'][0]['knowledge_refs'],[])
        self.assertTrue(task['draft_report']['logics'][0]['reference_notes'])
        self.assertEqual(task['ledger']['used']['model_calls'],0);self.assertEqual(task['ledger']['used']['data_requests'],0)
    def test_submission_is_durable_before_a_normalization_failure(self):
        with patch('researchhelper.draft_workflow.normalize_report',side_effect=ContractError('Controlled normalization failure')):
            self.session.draft(self.task['id'],report=deepcopy(PROPOSAL),data_requests=[])
        stored=self.session.archive.get(self.task['id'])
        self.assertEqual(stored['draft_submissions'][0]['payload'],PROPOSAL)
        self.assertFalse(stored.get('delivery'));self.assertEqual(stored['ledger']['used']['model_calls'],0)
    def test_diagnostics_never_enter_client_or_detailed_publications(self):
        task=self.run_report();self.assertEqual(task['draft_submissions'][0]['payload']['private_debug'],SENTINEL)
        frozen=self.session.archive.report(task['id'],1)
        self.assertNotIn('draft_submissions',frozen['content']);self.assertNotIn(SENTINEL,canonical(frozen['content']))
        for profile in ('standard','detailed'):
            raw=self.session.export(task['id'],profile=profile).read_text()
            self.assertNotIn(SENTINEL,raw);self.assertNotIn('draft_submissions',raw)
    def test_update_clears_current_submission_but_preserves_frozen_history(self):
        first=self.run_report();digest=first['delivery']['digest']
        updated=self.session.agent.prepare_update(first['id'],'2026-01-02')
        self.assertNotIn('draft_submissions',updated)
        self.assertEqual(self.session.archive.report(first['id'],1)['digest'],digest)
        with self.session.archive.connect() as db:
            body=db.execute('SELECT body FROM versions WHERE task_id=? AND version=1',(first['id'],)).fetchone()[0]
            old=decode_body(db,body)
        self.assertEqual(old['draft_submissions'][0]['payload'],PROPOSAL)
    def test_absent_proposal_does_not_fabricate_a_submission(self):
        task=self.session.draft(self.task['id'],report=None,data_requests=[])
        self.assertTrue(task['delivery']);self.assertNotIn('draft_submissions',task)
    def test_invalid_host_value_is_rejected_before_dispatch(self):
        proposal=deepcopy(PROPOSAL);proposal['private_debug']=float('nan')
        with self.assertRaisesRegex(ContractError,'非有限值'):
            self.run_report(proposal)
        task=self.session.archive.get(self.task['id'])
        self.assertNotIn('draft_submissions',task)
        self.assertFalse(task.get('delivery'))
        self.assertEqual(task['ledger']['used']['model_calls'],0)

if __name__=='__main__':unittest.main()
