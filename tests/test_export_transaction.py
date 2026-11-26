"""Failed writes/conversions keep prior exports; complete bundles become visible together."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import tempfile
import threading
import unittest
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

    def test_complete_html_from_another_edition_or_profile_cannot_be_exported(self):
        other = self.session.agent.start(self.session.agent.create_case('etf')['id'])
        other_content = self.session.agent.export(other['id'])['content']
        for content, profile in ((other_content, 'standard'), (self.content, 'detailed')):
            with self.subTest(profile=profile):
                wrong = exporter.design(content, ROOT, profile=profile)
                with patch.object(exporter, 'design', return_value=wrong):
                    with self.assertRaisesRegex(ContractError, '报告版本或详略不一致'):
                        exporter.export_report(self.content, ROOT, self.outputs, profile='standard', format='docx')
                self.assertEqual(list(self.outputs.iterdir()), [])

    def test_receipt_failure_keeps_only_prior_complete_bundle(self):
        previous=exporter.export_report(self.content,ROOT,self.outputs);entries=set(self.outputs.iterdir())
        convert=exporter.convert_saved_html
        def collide(source,*args,**kwargs):
            result=convert(source,*args,**kwargs)
            (Path(source).parent/'receipt.json').mkdir()
            return result
        with patch.object(exporter,'convert_saved_html',side_effect=collide):
            with self.assertRaises(OSError):exporter.export_report(self.content,ROOT,self.outputs,profile='detailed')
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
        file=Path(receipt['path']);self.assertTrue(observed)
        self.assertTrue(file.is_file())
        self.assertEqual(file,Path(receipt['source_html']))
        self.assertEqual(json.loads((file.parent/'receipt.json').read_text()),receipt)
        self.assertEqual(json.loads(file.with_suffix('.json').read_text()),self.content)
        self.assertEqual(sha256(file.read_bytes()).hexdigest(),receipt['sha256'])
        self.assertEqual(receipt['sha256'],receipt['source_html_sha256'])
        self.assertEqual(list(self.outputs.iterdir()),[file.parent])
        self.assertEqual(set(file.parent.iterdir()),{file,file.with_suffix('.json'),file.parent/'receipt.json'})

    def test_profiled_skill_html_reuses_identical_bytes_and_distinguishes_presentation(self):
        task_id = self.task['id']
        first = self.session.export(task_id,profile='standard')
        original_stat = first.stat()
        self.assertEqual(self.session.export(task_id,profile='standard'),first)
        repeated_stat = first.stat()
        self.assertEqual((repeated_stat.st_ino,repeated_stat.st_mtime_ns),(original_stat.st_ino,original_stat.st_mtime_ns))
        self.assertEqual(list(first.parent.parent.rglob('*.html')),[first])

        detailed = self.session.export(task_id,profile='detailed')
        themed = self.session.export(task_id,profile='standard',visual_theme='porcelain')
        self.assertEqual(self.session.export(task_id,profile='standard',visual_theme='porcelain'),themed)
        self.assertEqual(len({first.parent,detailed.parent,themed.parent}),3)
        self.assertEqual({path.with_suffix('.json').read_text() for path in (first,detailed,themed)},{canonical(self.content)})

    def test_profiled_html_reuses_receipt_with_multiple_custom_charts(self):
        from researchhelper.report_designer import compatible_schemes,design
        from tests.test_client_report_audience import fixture

        _, content = fixture()
        specs = design(content,ROOT)['document']['visual_sources']
        choices = {target:compatible_schemes(spec)[0] for target,spec in sorted(specs.items(),reverse=True)
                   if compatible_schemes(spec)}
        self.assertGreaterEqual(len(choices),2)
        first = exporter.export_report(content,ROOT,self.outputs,visual_profile={'schemes':choices})
        second = exporter.export_report(content,ROOT,self.outputs,visual_profile={'schemes':choices})
        self.assertEqual(first,second)
        self.assertEqual(first['visual_profile']['schemes'],choices)
        self.assertEqual(list(self.outputs.rglob('*.html')),[Path(first['path'])])

    def test_repeat_skill_exports_skip_conversion_in_each_static_format(self):
        task_id = self.task['id']
        formats = ('pdf','docx','xlsx','csv')
        first = {format:self.session.export(task_id,profile='standard',format=format) for format in formats}
        with patch.object(exporter,'convert_saved_html',side_effect=AssertionError('Repeated conversion')):
            for format, path in first.items():
                with self.subTest(format=format):
                    self.assertEqual(self.session.export(task_id,profile='standard',format=format),path)
                    receipt = json.loads((path.parent/'receipt.json').read_text())
                    self.assertEqual(sha256(path.read_bytes()).hexdigest(),receipt['sha256'])
                    self.assertEqual(sha256((path.parent/'report.html').read_bytes()).hexdigest(),receipt['source_html_sha256'])
                    self.assertEqual(path.with_suffix('.json').read_text(),canonical(self.content))
        self.assertEqual(set(first['pdf'].parent.parent.iterdir()),{path.parent for path in first.values()})

    def test_edited_converted_output_and_receipt_are_preserved(self):
        first = exporter.export_report(self.content,ROOT,self.outputs,format='docx')
        edited = Path(first['path'])
        user_bytes = edited.read_bytes()+b'User revision'
        edited.write_bytes(user_bytes)
        restored = exporter.export_report(self.content,ROOT,self.outputs,format='docx')
        self.assertNotEqual(restored['path'],first['path'])
        self.assertEqual(edited.read_bytes(),user_bytes)
        self.assertEqual(restored,exporter.export_report(self.content,ROOT,self.outputs,format='docx'))

        receipt = Path(restored['path']).parent/'receipt.json'
        receipt.write_bytes(b'User receipt note')
        recovered = exporter.export_report(self.content,ROOT,self.outputs,format='docx')
        self.assertNotIn(recovered['path'],{first['path'],restored['path']})
        self.assertEqual(receipt.read_bytes(),b'User receipt note')
        self.assertEqual(recovered,exporter.export_report(self.content,ROOT,self.outputs,format='docx'))

    def test_converted_cache_is_bound_to_presentation_and_converter_revision(self):
        original = exporter.export_report(self.content,ROOT,self.outputs,format='csv')
        changed_profile = exporter.export_report(self.content,ROOT,self.outputs,format='csv',profile='detailed')
        changed_theme = exporter.export_report(self.content,ROOT,self.outputs,format='csv',theme='porcelain')
        with patch.object(exporter,'_CONVERSION_REVISION',exporter._CONVERSION_REVISION+1):
            upgraded = exporter.export_report(self.content,ROOT,self.outputs,format='csv')
            self.assertEqual(upgraded,exporter.export_report(self.content,ROOT,self.outputs,format='csv'))
        self.assertEqual(len({row['path'] for row in (original,changed_profile,changed_theme,upgraded)}),4)
        self.assertEqual(original['source_html_sha256'],upgraded['source_html_sha256'])
        self.assertEqual(original,exporter.export_report(self.content,ROOT,self.outputs,format='csv'))

    def test_concurrent_converted_exports_publish_one_verified_bundle(self):
        barrier = threading.Barrier(2)
        convert = exporter.convert_saved_html

        def synchronized(*args,**kwargs):
            result = convert(*args,**kwargs)
            barrier.wait(timeout=8)
            return result

        with patch.object(exporter,'convert_saved_html',side_effect=synchronized), ThreadPoolExecutor(max_workers=2) as workers:
            calls = [workers.submit(exporter.export_report,self.content,ROOT,self.outputs,format='xlsx') for _ in range(2)]
            first, second = [call.result(timeout=15) for call in calls]
        file = Path(first['path'])
        self.assertEqual(first,second)
        self.assertEqual(list(self.outputs.iterdir()),[file.parent])
        self.assertEqual(json.loads((file.parent/'receipt.json').read_text()),first)
        self.assertEqual(sha256(file.read_bytes()).hexdigest(),first['sha256'])

    def test_changed_html_bundle_is_preserved_and_healthy_recovery_is_reused(self):
        first = exporter.export_report(self.content,ROOT,self.outputs)
        original = Path(first['path'])
        sidecar = original.with_suffix('.json')
        sidecar.write_bytes(b'User edited metadata')
        second = exporter.export_report(self.content,ROOT,self.outputs)
        self.assertNotEqual(first['path'],second['path'])
        self.assertEqual(sidecar.read_bytes(),b'User edited metadata')
        self.assertEqual(sha256(original.read_bytes()).hexdigest(),first['sha256'])
        self.assertEqual(second,exporter.export_report(self.content,ROOT,self.outputs))

        receipt_file = Path(second['path']).parent/'receipt.json'
        receipt_file.write_bytes(b'User edited receipt')
        third = exporter.export_report(self.content,ROOT,self.outputs)
        self.assertNotIn(third['path'],{first['path'],second['path']})
        self.assertEqual(receipt_file.read_bytes(),b'User edited receipt')
        self.assertEqual(third,exporter.export_report(self.content,ROOT,self.outputs))
        self.assertEqual(len(list(self.outputs.rglob('*.html'))),3)

    def test_concurrent_profiled_html_exports_publish_one_complete_bundle(self):
        barrier = threading.Barrier(2)
        convert = exporter.convert_saved_html

        def synchronized(*args,**kwargs):
            result = convert(*args,**kwargs)
            barrier.wait(timeout=8)
            return result

        with patch.object(exporter,'convert_saved_html',side_effect=synchronized), ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(exporter.export_report,self.content,ROOT,self.outputs) for _ in range(2)]
            first, second = [future.result(timeout=15) for future in futures]
        file = Path(first['path'])
        self.assertEqual(first,second)
        self.assertEqual(list(self.outputs.iterdir()),[file.parent])
        self.assertEqual(list(file.parent.glob('*.html')),[file])
        self.assertEqual(json.loads((file.parent/'receipt.json').read_text()),first)
        self.assertEqual(file.with_suffix('.json').read_text(),canonical(self.content))

    def test_directory_publish_failure_removes_stage_and_preserves_prior_bundle(self):
        prior=exporter.export_report(self.content,ROOT,self.outputs);entries=set(self.outputs.iterdir())
        original=exporter.os.replace
        def fail_directory(source,target):
            if Path(source).is_dir():raise OSError('Controlled final publication failure')
            return original(source,target)
        with patch.object(exporter.os,'replace',side_effect=fail_directory):
            with self.assertRaises(OSError):exporter.export_report(self.content,ROOT,self.outputs,profile='detailed')
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

    def test_edited_frozen_original_reuses_one_recovery_under_concurrent_exports(self):
        first = self.session.export(self.task['id'])
        original = first.with_suffix('.json')
        original.write_bytes(b'User notes')
        barrier = threading.Barrier(2)
        write = exporter._atomic_write

        def synchronized(file, data):
            write(file, data)
            if Path(file).suffix == '.json':
                barrier.wait(timeout=8)

        with patch.object(exporter,'_atomic_write',side_effect=synchronized), ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(self.session.export,self.task['id']) for _ in range(2)]
            left, right = [future.result(timeout=15) for future in futures]
        self.assertEqual(left,right)
        self.assertNotEqual(first,left)
        with patch.object(exporter,'_atomic_write',side_effect=AssertionError('Repeated write')):
            self.assertEqual(self.session.export(self.task['id']),left)
        self.assertEqual(set(first.parent.parent.iterdir()),{first.parent,left.parent})
        self.assertEqual(original.read_bytes(),b'User notes')

    def test_legacy_frozen_recovery_is_reused_only_if_both_files_match(self):
        first = self.session.export(self.task['id'])
        root = first.parent.parent
        valid = root/(first.parent.name+'-a1b2c3d4e5f6')
        damaged = root/(first.parent.name+'-0a2b3c4d5e6f')
        for target in (valid,damaged):
            target.mkdir()
            (target/first.name).write_bytes(first.read_bytes())
            (target/first.with_suffix('.json').name).write_bytes(first.with_suffix('.json').read_bytes())
        (damaged/first.name).write_bytes(b'User edited old HTML')
        first.with_suffix('.json').write_bytes(b'User notes')
        self.assertEqual(self.session.export(self.task['id']),valid/first.name)
        self.assertEqual(set(root.iterdir()),{first.parent,valid,damaged})
        self.assertEqual((damaged/first.name).read_bytes(),b'User edited old HTML')

    def test_frozen_export_reuses_primary_published_during_directory_scan(self):
        first = self.session.export(self.task['id'])
        root = first.parent.parent
        staged = self.project/'another-writer'
        first.parent.rename(staged)
        iterdir = Path.iterdir

        def publish_during_scan(path):
            entries = list(iterdir(path))
            if path == root and staged.is_dir():
                staged.rename(first.parent)
            return iter(entries)

        with patch.object(Path,'iterdir',publish_during_scan):
            self.assertEqual(self.session.export(self.task['id']),first)
        self.assertEqual(list(root.iterdir()),[first.parent])

    def test_profiled_recovery_is_reused_after_earlier_export_names_are_vacated(self):
        for format in ('html','csv'):
            with self.subTest(format=format):
                deliver = lambda: exporter.export_report(self.content,ROOT,self.outputs,format=format)
                edited = Path(deliver()['path'])
                edited.write_bytes(b'User draft')
                restored = deliver()
                removed = self.project/('relocated-'+format)
                edited.parent.rename(removed)
                with patch.object(exporter,'convert_saved_html',side_effect=AssertionError('Unneeded conversion')):
                    self.assertEqual(deliver(),restored)
                self.assertFalse(edited.parent.exists())
                self.assertEqual((removed/edited.name).read_bytes(),b'User draft')

    def test_recovery_slot_checks_filesystem_aliases_before_publishing(self):
        primary = self.outputs/'export-test'
        primary.mkdir()
        existing = self.outputs/'EXPORT-TEST-1'
        existing.mkdir()
        note = existing/'saved.txt'
        note.write_text('原有交付')
        first = self.outputs/'export-test-1'
        expected = self.outputs/'export-test-2' if first.exists() else first
        path, receipt = exporter._bundle_location(self.outputs, primary.name, lambda _: None)
        self.assertEqual(path, expected)
        self.assertIsNone(receipt)
        self.assertEqual(note.read_text(), '原有交付')

    def test_case_renamed_complete_recovery_reuses_the_recorded_path(self):
        previous = Path(exporter.export_report(self.content, ROOT, self.outputs, format='csv')['path'])
        previous.write_bytes(b'user-edited')
        recovery = exporter.export_report(self.content, ROOT, self.outputs, format='csv')
        original = Path(recovery['path']).parent
        intermediate = self.outputs/'rename-staging'
        original.rename(intermediate)
        renamed = original.with_name(original.name.upper())
        intermediate.rename(renamed)
        if original.exists():
            with patch.object(exporter, '_export_transaction', side_effect=AssertionError('Should reuse verified recovery')):
                self.assertEqual(exporter.export_report(self.content, ROOT, self.outputs, format='csv'), recovery)
        else:
            fresh = exporter.export_report(self.content, ROOT, self.outputs, format='csv')
            self.assertEqual(fresh['path'], recovery['path'])
        self.assertEqual(previous.read_bytes(), b'user-edited')
        self.assertTrue((renamed/'receipt.json').is_file())

    def test_concurrent_frozen_exports_return_the_same_complete_pair(self):
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
