"""Failed writes/conversions keep prior exports; complete bundles become visible together."""
from copy import deepcopy
from pathlib import Path
import tempfile,unittest
from unittest.mock import patch
from tests import ROOT
from core.session import Session
from researchhelper import artifact_exporter as exporter
from researchhelper.contracts import ContractError,canonical


class ExportTransactionTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='rh-export-transaction-');self.addCleanup(folder.cleanup)
        self.project=Path(folder.name).resolve();self.session=Session(self.project);self.addCleanup(self.session.agent.close_runtime)
        self.task=self.session.agent.start(self.session.agent.create_case('mechanism')['id'])
        self.report=self.session.agent.export(self.task['id']);self.content=self.report['content']
        self.outputs=self.project/'outputs';self.outputs.mkdir()

    def test_atomic_file_sync_failure_leaves_prior_file_and_no_temporary(self):
        file=self.outputs/'prior.html';file.write_bytes(b'previous complete report')
        with patch.object(exporter.os,'fsync',side_effect=OSError('Controlled sync failure')):
            with self.assertRaises(OSError):exporter._atomic_write(file,b'replacement')
        self.assertEqual(file.read_bytes(),b'previous complete report')
        self.assertEqual(list(self.outputs.iterdir()),[file])

    def test_conversion_failure_does_not_leave_an_incomplete_export_directory(self):
        previous=exporter.export_report(self.content,ROOT,self.outputs);before=Path(previous['path']).read_bytes()
        entries=set(self.outputs.iterdir())
        with patch.object(exporter,'convert_saved_html',side_effect=ContractError('Controlled conversion failure')):
            with self.assertRaises(ContractError):exporter.export_report(self.content,ROOT,self.outputs,format='docx')
        self.assertEqual(set(self.outputs.iterdir()),entries)
        self.assertEqual(Path(previous['path']).read_bytes(),before)

    def test_receipt_failure_keeps_only_prior_complete_bundle(self):
        previous=exporter.export_report(self.content,ROOT,self.outputs);entries=set(self.outputs.iterdir())
        convert=exporter.convert_saved_html
        def collide(source,*args,**kwargs):
            result=convert(source,*args,**kwargs)
            (Path(source).parent/'receipt.json').mkdir()
            return result
        with patch.object(exporter,'convert_saved_html',side_effect=collide):
            with self.assertRaises(OSError):exporter.export_report(self.content,ROOT,self.outputs)
        self.assertEqual(set(self.outputs.iterdir()),entries)
        self.assertTrue(Path(previous['path']).is_file())

    def test_default_frozen_export_keeps_existing_legacy_file_on_sidecar_collision(self):
        directory=self.project/'result/researchhelper'/self.task['id'];directory.mkdir(parents=True)
        prior=directory/'research-v1.html';prior.write_bytes(b'<!doctype html><title>Previously saved client report</title>')
        metadata=prior.with_suffix('.json');metadata.mkdir()
        before=prior.read_bytes()
        try:output=self.session.export(self.task['id'])
        except OSError:output=None
        self.assertEqual(prior.read_bytes(),before)
        if output is not None:
            self.assertEqual(output.read_text(),self.report['html'])
            self.assertEqual(output.with_suffix('.json').read_text(),canonical(self.content))
        self.assertTrue(metadata.is_dir())

    def test_default_frozen_export_reuses_same_complete_pair_without_changing_task(self):
        before=deepcopy(self.session.status(self.task['id']))
        first=self.session.export(self.task['id']);snapshot={str(p):p.read_bytes() for p in first.parent.iterdir() if p.is_file()}
        second=self.session.export(self.task['id'])
        self.assertEqual(first,second);self.assertEqual(second.read_text(),self.report['html'])
        self.assertEqual(second.with_suffix('.json').read_text(),canonical(self.content))
        self.assertEqual({str(p):p.read_bytes() for p in first.parent.iterdir() if p.is_file()},snapshot)
        self.assertEqual(self.session.status(self.task['id']),before)


    def test_complete_bundle_paths_are_published_only_after_conversion(self):
        convert=exporter.convert_saved_html;observed=[]
        def inspect(source,*args,**kwargs):
            self.assertTrue(Path(source).is_file())
            observed.extend(path.name for path in self.outputs.iterdir())
            self.assertFalse(any(path.name.startswith('export-') for path in self.outputs.iterdir()))
            return convert(source,*args,**kwargs)
        with patch.object(exporter,'convert_saved_html',side_effect=inspect):
            receipt=exporter.export_report(self.content,ROOT,self.outputs)
        import json,hashlib
        file=Path(receipt['path']);self.assertTrue(observed)
        self.assertTrue(file.is_file() and Path(receipt['source_html']).is_file())
        self.assertEqual(json.loads((file.parent/'receipt.json').read_text()),receipt)
        self.assertEqual(json.loads(file.with_suffix('.json').read_text()),self.content)
        self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(),receipt['sha256'])
        self.assertEqual(list(self.outputs.iterdir()),[file.parent])

    def test_directory_publish_failure_removes_stage_and_preserves_prior_bundle(self):
        prior=exporter.export_report(self.content,ROOT,self.outputs);entries=set(self.outputs.iterdir())
        original=exporter.os.replace
        def fail_directory(source,target):
            if Path(source).is_dir():raise OSError('Controlled final publication failure')
            return original(source,target)
        with patch.object(exporter.os,'replace',side_effect=fail_directory):
            with self.assertRaises(OSError):exporter.export_report(self.content,ROOT,self.outputs)
        self.assertEqual(set(self.outputs.iterdir()),entries);self.assertTrue(Path(prior['path']).is_file())

    def test_frozen_pair_failure_does_not_change_research_or_leave_an_incomplete_pair(self):
        before=deepcopy(self.session.status(self.task['id']))
        with patch.object(exporter.os,'fsync',side_effect=OSError('Controlled sync failure')):
            with self.assertRaises(OSError):self.session.export(self.task['id'])
        directory=self.project/'result/researchhelper'/self.task['id']
        self.assertFalse(directory.exists() and any(directory.iterdir()))
        self.assertEqual(self.session.status(self.task['id']),before)

    def test_changed_frozen_pair_is_kept_and_a_complete_recovery_copy_is_returned(self):
        first=self.session.export(self.task['id']);metadata=first.with_suffix('.json')
        metadata.write_bytes(b'User edited metadata')
        second=self.session.export(self.task['id'])
        self.assertNotEqual(first,second);self.assertEqual(metadata.read_bytes(),b'User edited metadata')
        self.assertEqual(second.read_text(),self.report['html'])
        self.assertEqual(second.with_suffix('.json').read_text(),canonical(self.content))

    def test_concurrent_frozen_exports_return_the_same_complete_pair(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        barrier=threading.Barrier(2);write=exporter._atomic_write
        def synchronized(file,data):
            write(file,data)
            if Path(file).suffix=='.json':barrier.wait(timeout=8)
        with patch.object(exporter,'_atomic_write',side_effect=synchronized),ThreadPoolExecutor(max_workers=2) as workers:
            futures=[workers.submit(self.session.export,self.task['id']) for _ in range(2)]
            files=[future.result(timeout=15) for future in futures]
        self.assertEqual(files[0],files[1]);self.assertEqual(files[0].read_text(),self.report['html'])
        self.assertEqual(files[0].with_suffix('.json').read_text(),canonical(self.content))
        self.assertEqual(list(files[0].parent.parent.iterdir()),[files[0].parent])

    def test_explicit_invalid_versions_do_not_silently_export_the_latest_report(self):
        for version in (True,False,0,-1,'1',1.0):
            with self.subTest(version=version),self.assertRaises(ContractError):
                self.session.export(self.task['id'],version=version)


    def test_default_export_keeps_the_selected_version_when_a_new_run_starts(self):
        later=deepcopy(self.task);later.update(version=2,status='运行中')
        # Only v1 is delivered. Simulate v2 becoming current after the export
        # selected v1, without changing or synthesizing a financial report.
        for profile in (None,'standard'):
            with self.subTest(profile=profile),patch.object(self.session.agent,'_get',return_value=later):
                output=self.session.export(self.task['id'],profile=profile)
            self.assertTrue(output.is_file())
            import json
            self.assertEqual(json.loads(output.with_suffix('.json').read_text())['research_version'],1)


    def test_provided_falsey_profiles_and_themes_do_not_become_default_choices(self):
        for field in ('profile','visual_theme'):
            for value in ('',False,0,[],{}):
                with self.subTest(field=field,value=value),self.assertRaises(ContractError):
                    self.session.export(self.task['id'],format='csv',**{field:value})
        directory=self.project/'result/researchhelper'/self.task['id']/'exports'
        self.assertFalse(directory.exists() and any(directory.iterdir()))


    def test_atomic_write_supports_an_already_valid_long_filename(self):
        file=self.outputs/('研'*80+'.html')
        exporter._atomic_write(file,b'complete report')
        self.assertEqual(file.read_bytes(),b'complete report')
        self.assertEqual(list(self.outputs.iterdir()),[file])

    def test_long_chinese_title_keeps_a_valid_report_and_sidecar_filename(self):
        task=self.session.create('仅验证离线报告的文件名')
        done=self.session.draft(task['id'],report=dict(title='研究报告标题'*30,conclusion='离线文件名验收。',
            logics=[dict(title='保留说明',analysis='此案例仅检查输出文件名。',implication='无投资判断。',risk='无市场数据。')],risks=[]))
        self.assertTrue(done.get('delivery'))
        content=self.session.agent.export(done['id'])['content']
        receipt=exporter.export_report(content,ROOT,self.outputs)
        file=Path(receipt['path'])
        self.assertTrue(file.is_file() and file.with_suffix('.json').is_file())
        self.assertLessEqual(len(file.name.encode('utf-8')),240)
        self.assertIn('研究报告标题',file.name)


if __name__=='__main__':unittest.main()
