"""Fault injection at the one-click macOS candidate and download handoff."""
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from tests import test_current_delivery_offline as delivery_fixture
import accept_current as acceptance
import build_current as delivery
from source_snapshot import digest


class DownloadRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = delivery_fixture.CurrentDeliveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.dist = self.fixture.root, self.fixture.dist
        binding = patch.object(delivery, 'verify_release_binding')
        binding.start()
        self.addCleanup(binding.stop)
        self.accepted_count = 0

    def candidate(self):
        result = delivery.build_current(self.root, builder=self.fixture.builder, install_skill=False)
        return Path(result['payload']) / 'candidate.json'

    def accept(self, selected, root):
        self.accepted_count += 1
        candidate = delivery.verify_current_build(selected, root, check_downloads=False)
        folder = root / 'result/acceptance' / ('report-' + str(self.accepted_count))
        folder.mkdir(parents=True)
        receipt = folder / 'receipt.json'
        result = dict(status='scoped_passed', candidate=str(selected), manifest_sha256=candidate['manifest_sha256'],
                      dataset_parity=True, service_stopped=True, mount_detached=True,
                      temporary_research_removed=True, receipt=str(receipt))
        delivery.write_record(receipt, result)
        return result

    def install(self, selected, root):
        delivery.verify_current_build(selected, root)
        return {'status': 'already_current'}

    def test_loss_at_end_of_acceptance_restores_both_exact_files(self):
        def remove_after_acceptance(selected, root):
            result = self.accept(selected, root)
            shutil.rmtree(self.dist)
            return result
        with patch.object(acceptance, 'accept_current', side_effect=remove_after_acceptance), \
             patch.object(delivery, 'update_project_skill', side_effect=self.install):
            result = delivery.build_and_accept(self.root, builder=self.fixture.builder)
        self.assertEqual(self.accepted_count, 1)
        self.assertEqual(result['download_repairs'], [{'phase': 'after_acceptance', 'files': sorted(p.name for p in self.dist.iterdir())}])
        for artifact in result['artifacts'].values():
            self.assertEqual(digest(self.dist / Path(artifact['path']).name), artifact['sha256'])
        self.assertFalse(list(self.root.glob('.download-recovery-*')))

    def test_next_invocation_reuses_completed_acceptance_after_interrupted_skill_update(self):
        with patch.object(acceptance, 'accept_current', side_effect=self.accept), \
             patch.object(delivery, 'update_project_skill', side_effect=RuntimeError('interrupted skill update')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted skill update'):
                delivery.build_and_accept(self.root, builder=self.fixture.builder)
        selected, first = delivery.active_macos_candidate(self.root)
        skill = self.dist / Path(first['artifacts']['skill']['path']).name
        skill.unlink()
        with patch.object(acceptance, 'accept_current', side_effect=AssertionError('acceptance already completed')), \
             patch.object(delivery, 'update_project_skill', side_effect=self.install):
            result = delivery.build_and_accept(self.root, builder=lambda **_: self.fail('unexpected rebuild'))
        self.assertEqual(first['payload'], str(selected.parent))
        self.assertEqual(first['postbuild_acceptance'], result['postbuild_acceptance'])
        self.assertEqual(result['download_repairs'], [{'phase': 'after_acceptance', 'files': [skill.name]}])
        self.assertEqual(digest(skill), first['artifacts']['skill']['sha256'])

    def test_interrupted_acceptance_resumes_the_prepared_candidate_when_dist_is_missing(self):
        selected = self.candidate()
        shutil.rmtree(self.dist)
        with patch.object(acceptance, 'accept_current', side_effect=self.accept), \
             patch.object(delivery, 'update_project_skill', side_effect=self.install):
            result = delivery.build_and_accept(self.root, builder=lambda **_: self.fail('unexpected rebuild'))
        self.assertEqual(result['payload'], str(selected.parent))
        self.assertEqual(self.accepted_count, 1)
        self.assertEqual(len(list(self.dist.iterdir())), 2)

    def test_modified_or_unknown_downloads_are_preserved(self):
        selected = self.candidate()
        self.fixture.old.write_bytes(b'edited by user')
        (self.dist / 'ResearchHelper-Skill-0.10.0.zip').unlink()
        with delivery.build_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, '下载与保留候选不一致'):
                delivery.verify_or_restore_downloads(selected, self.root, 'test')
        self.assertEqual(self.fixture.old.read_bytes(), b'edited by user')
        note = self.dist / 'notes.md'
        note.write_text('keep this')
        with delivery.build_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, '未知项目'):
                delivery.verify_or_restore_downloads(selected, self.root, 'test')
        self.assertEqual(note.read_text(), 'keep this')

    def test_missing_mirror_never_uses_a_corrupt_archive_or_an_unbound_candidate(self):
        selected = self.candidate()
        shutil.rmtree(self.dist)
        marker = delivery.macos_candidate_marker(self.root)
        marker_bytes = marker.read_bytes()
        marker.unlink()
        with delivery.build_lock(self.root):
            with self.assertRaises(delivery.MissingDownloadCopies):
                delivery.verify_or_restore_downloads(selected, self.root, 'test')
        self.assertFalse(self.dist.exists())
        marker.write_bytes(marker_bytes)
        candidate = delivery.verify_current_build(selected, self.root, check_downloads=False)
        Path(candidate['artifacts']['skill']['path']).write_bytes(b'corrupt archived bytes')
        with delivery.build_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, '归档已变化'):
                delivery.verify_or_restore_downloads(selected, self.root, 'test')
        self.assertFalse(self.dist.exists())

    def test_source_changes_force_a_new_build_instead_of_restoring_old_downloads(self):
        selected = self.candidate()
        shutil.rmtree(self.dist)
        (self.root / 'VERSION').write_text('0.10.1')
        with delivery.build_lock(self.root):
            self.assertIsNone(delivery.resumable_macos_candidate(self.root))
            with self.assertRaisesRegex(RuntimeError, '源码或源清单已变化'):
                delivery.verify_or_restore_downloads(selected, self.root, 'test')
        self.assertFalse(self.dist.exists())

    def test_archive_acceptance_survives_loss_at_its_final_verification(self):
        selected = self.candidate()
        (self.root / 'result/acceptance').mkdir()

        class OfflineRuntime:
            def __init__(runtime, workspace, record):
                runtime.mount, runtime.record = workspace, record

            def start(runtime):
                return 'http://127.0.0.1:1/'

            def stop(runtime):
                runtime.record.update(service_stopped=True, mount_detached=True)

        def finish_layout(*_):
            shutil.rmtree(self.dist)
            return {'fixture': True}

        with patch('tests.accept_native_workbench_render.RenderRuntime', OfflineRuntime), \
             patch.object(acceptance, 'accept_package', return_value={}), \
             patch.object(acceptance, 'accept_app_reports', return_value=[]), \
             patch.object(acceptance, 'accept_skill_reports', return_value=[]), \
             patch.object(acceptance, 'verify_report_parity'), \
             patch.object(acceptance, 'accept_shared_native_print', side_effect=finish_layout):
            record = acceptance.accept_current(selected, self.root)
        self.assertEqual(record['status'], 'scoped_passed')
        self.assertTrue(record['temporary_research_removed'])
        self.assertFalse(self.dist.exists())
