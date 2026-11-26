"""A removed composer attachment must never re-enter an unstarted study."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import hashlib, importlib.util, os, socket, tempfile, threading, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
RESOURCES=Path(os.environ.get("RESEARCHHELPER_TEST_RESOURCES",ROOT))
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, canonical
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import extract_material
from researchhelper.draft_workflow import _writing_messages

PAYLOAD=b'REMOVE_ONLY_20261111: private test input, not a financial fact.'
KEEP=b'KEEP_ONLY_20261111: the independently selected test original.'
DIGEST=hashlib.sha256(PAYLOAD).hexdigest()

class PendingMaterialSelectionTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='pending-material-',dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup);self.root=Path(folder.name)
        self.archive=ResearchArchive(self.root/'store')
        self.agent=ResearchAgent(self.archive,RESOURCES,offline=True,draft_first=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'))
        guard.start();self.addCleanup(guard.stop)
        self.task=self.agent.create('核对本次选择的材料，不作投资判断。',research_mode='current')
    def current(self):return self.archive.get(self.task['id'])
    def upload(self,name='remove.txt',payload=PAYLOAD):
        task=self.current()
        return self.agent.add_material(task['id'],name,payload,expected_version=task['version'],expected_revision=task['revision'])
    def remove(self,name='remove.txt',digest=DIGEST,**options):
        task=self.current()
        return self.agent.remove_material(task['id'],name,digest,expected_version=task['version'],expected_revision=task['revision'],**options)
    def test_removed_input_is_absent_from_current_sources_and_writing(self):
        self.upload();before=deepcopy(self.current());self.upload('keep.txt',KEEP)
        done=self.remove()
        self.assertEqual([r['name'] for r in done['materials']],['keep.txt'])
        messages=canonical(_writing_messages(done,[]))
        self.assertNotIn('REMOVE_ONLY_20261111',messages);self.assertIn('KEEP_ONLY_20261111',messages)
        self.assertEqual(done['ledger'],before['ledger'])
        for key in ('id','version','batch','input_hash','question','original_input','cutoff'):
            self.assertEqual(done[key],before[key])
    def test_original_bytes_survive_without_becoming_a_parse_recovery_prompt(self):
        self.upload();original=self.archive.retained_originals(self.current())[0]
        done=self.remove();metadata=self.archive.retained_originals(done)
        self.assertEqual(self.archive.original(done,original['id'])[1],PAYLOAD)
        self.assertTrue(metadata[0]['excluded_before_start'])
        self.assertEqual(metadata[0]['sha256'],DIGEST)
        self.assertEqual(self.archive.retained_originals(done,pending_only=True),[])
        with self.assertRaisesRegex(ContractError,'已移出'):
            self.agent.retry_original(done['id'],original['id'])
    def test_unknown_outcome_is_fenced_before_a_delayed_request_arrives(self):
        stale=self.current();done=self.remove()
        self.assertFalse(done['materials'])
        parser=SimpleNamespace(parse=__import__('unittest.mock').mock.Mock())
        self.agent.material_parser=parser
        with self.assertRaisesRegex(ContractError,'选择已变化'):
            self.agent.add_material(stale['id'],'remove.txt',PAYLOAD,expected_version=stale['version'],expected_revision=stale['revision'])
        parser.parse.assert_not_called()
        self.assertEqual(self.archive.retained_originals(self.current()),[])
    def test_a_late_parser_cannot_reinsert_an_excluded_file(self):
        started=threading.Event();release=threading.Event()
        def parse(name,payload):
            started.set();self.assertTrue(release.wait(5));return extract_material(name,payload)
        self.agent.material_parser=SimpleNamespace(parse=parse)
        task=self.current()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.agent.add_material,task['id'],'remove.txt',PAYLOAD,expected_version=task['version'],expected_revision=task['revision'])
            try:
                self.assertTrue(started.wait(5));self.remove()
            finally:release.set()
            with self.assertRaisesRegex(ContractError,'上传期间已移出'):future.result(timeout=5)
        self.assertEqual(self.current()['materials'],[])
        originals=self.archive.retained_originals(self.current())
        self.assertEqual(len(originals),1);self.assertTrue(originals[0]['excluded_before_start'])
    def test_an_independent_inflight_file_is_not_discarded(self):
        self.upload();started=threading.Event();release=threading.Event()
        def parse(name,payload):
            started.set();self.assertTrue(release.wait(5));return extract_material(name,payload)
        self.agent.material_parser=SimpleNamespace(parse=parse);task=self.current()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.agent.add_material,task['id'],'keep.txt',KEEP,expected_version=task['version'],expected_revision=task['revision'])
            try:self.assertTrue(started.wait(5));self.remove()
            finally:release.set()
            result=future.result(timeout=5)
        self.assertEqual([r['name'] for r in result['materials']],['keep.txt'])
    def test_an_explicit_later_upload_can_readopt_the_same_file(self):
        self.upload();self.remove();result=self.upload()
        self.assertEqual(len(result['materials']),1);self.assertEqual(result.get('material_exclusions'),[])
        original=self.archive.retained_originals(result)[0]
        self.assertNotIn('excluded_before_start',original)
        self.assertEqual(self.archive.original(result,original['id'])[1],PAYLOAD)
    def test_duplicate_filenames_keep_the_other_selected_content_copy(self):
        first=self.upload();identity=first['materials'][0]['id'];second=self.upload('copy.txt')
        self.assertEqual([r['id'] for r in second['materials']],[identity])
        done=self.remove(retained_files=[dict(name='copy.txt',sha256=DIGEST)])
        self.assertEqual([r['id'] for r in done['materials']],[identity])
        final=self.remove('copy.txt');self.assertEqual(final['materials'],[])
        self.assertEqual(len(self.archive.retained_originals(final)),2)
    def test_submitted_attachment_names_follow_their_own_question(self):
        self.task = self.agent.create('不联网，只核对附件原文。请摘录维护安排。')
        self.upload('原件.txt')
        self.upload('原件副本.txt')
        self.upload('原件.txt', KEEP)
        self.upload()
        self.remove(retained_files=[dict(name='原件.txt', sha256=DIGEST),
                                    dict(name='原件副本.txt', sha256=DIGEST)])
        self.assertNotIn('attachments', self.current()['messages'][0])
        first = self.agent.start(self.task['id'])
        self.assertTrue(first['delivery'])
        self.assertEqual(first['messages'][0]['attachments'], ['原件.txt', '原件副本.txt', '原件.txt'])
        self.upload('后补资料.txt', b'After submission')
        reloaded = ResearchArchive(self.root/'store').get(first['id'])
        self.assertEqual(reloaded['messages'], first['messages'])
        self.assertEqual(reloaded['delivery'], first['delivery'])

        followup = self.agent.follow_up(first['id'], '只用新附件核对开放时间，不联网。', start=False)
        self.assertNotIn('attachments', followup['messages'][-1])
        self.agent.add_material(followup['id'], '开放安排.md', '周一至周五开放。'.encode())
        second = self.agent.start(followup['id'])
        self.assertTrue(second['delivery'])
        self.assertEqual(second['messages'][0], first['messages'][0])
        self.assertEqual(second['messages'][-1]['attachments'], ['开放安排.md'])
    def test_a_still_selected_identical_file_is_a_noop(self):
        self.upload();before=self.current()
        result=self.remove(retained_files=[dict(name='remove.txt',sha256=DIGEST)])
        self.assertEqual(result,before);self.assertEqual(self.current(),before)
    def test_stale_version_or_revision_cannot_modify_current_inputs(self):
        self.upload();before=self.current()
        for v,r in ((before['version']+1,before['revision']),(before['version'],before['revision']-1)):
            with self.subTest(version=v,revision=r):
                with self.assertRaises(ContractError):
                    self.agent.remove_material(before['id'],'remove.txt',DIGEST,expected_version=v,expected_revision=r)
                self.assertEqual(self.current(),before)
    def test_started_or_spent_states_cannot_be_rewritten(self):
        self.upload();original=self.current()
        states=[dict(status=status,batch=1) for status in ('待开始','运行中','失败','已取消')]
        states.extend(dict(ledger={**deepcopy(original['ledger']),'used':{**original['ledger']['used'],field:1}}) for field in original['ledger']['used'])
        for changes in states:
            with self.subTest(changes=changes):
                task=self.current();task.update(deepcopy(original),revision=task['revision']);task.update(changes);self.archive.save(task)
                before=self.current()
                with self.assertRaises(ContractError):self.remove()
                self.assertEqual(self.current(),before)
    def test_a_frozen_report_and_its_originals_are_immutable(self):
        self.upload();done=self.agent.start(self.task['id'],dict(data_requests=[],draft_report=dict(title='材料核对',logics=[dict(title='范围',analysis='只用于核对冻结交付保护。')])))
        self.assertTrue(done.get('delivery'),done.get('error'));report=deepcopy(self.archive.report(done['id'],done['version']));before=self.current()
        with self.assertRaises(ContractError):self.remove()
        self.assertEqual(self.current(),before);self.assertEqual(self.archive.report(done['id'],done['version']),report)
    def test_save_failure_keeps_the_active_attachment_and_original_bytes(self):
        self.upload();before=self.current()
        with patch.object(self.archive,'save',side_effect=OSError('Controlled save failure')):
            with self.assertRaises(OSError):self.remove()
        self.assertEqual(self.current(),before)
        original=self.archive.retained_originals(before)[0]
        self.assertEqual(self.archive.original(before,original['id'])[1],PAYLOAD)
    def test_other_tasks_and_independent_raw_sources_stay_intact(self):
        self.upload();other=self.agent.create('独立任务',research_mode='current')
        self.agent.add_material(other['id'],'remove.txt',PAYLOAD)
        before_other=self.archive.get(other['id'])
        self.agent.add_snapshot(self.task['id'],'ifind','get_limit_policy',{},dict(errorcode=0,tables=[]))
        sources=deepcopy(self.current()['source_records']);done=self.remove()
        self.assertEqual(done['source_records'],sources);self.assertEqual(self.archive.get(other['id']),before_other)
    def test_invalid_keys_do_not_change_the_archive(self):
        self.upload();before=self.current()
        cases=[dict(name='../remove.txt'),dict(digest='not-a-hash'),dict(expected_revision=True),dict(retained_files=[dict(name='keep.txt',sha256='bad')])]
        for invalid in cases:
            with self.subTest(invalid=invalid):
                options=dict(ident=before['id'],name='remove.txt',digest=DIGEST,expected_version=before['version'],expected_revision=before['revision']);options.update(invalid)
                with self.assertRaises(ContractError):self.agent.remove_material(**options)
                self.assertEqual(self.current(),before)

if __name__=='__main__':unittest.main()
