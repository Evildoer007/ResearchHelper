"""Fixture-only mount serialization; no image, network or App is started."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests.accept_serial_synthesis_research import DiagnosticRuntime, image_lease
from researchhelper.contracts import ContractError


class CandidateImageLeaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='rh-image-lease-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.image = self.root / 'candidate.dmg'
        self.folder = self.root / 'lease'

    def test_unmounted_image_enters_without_starting_any_runtime(self):
        with patch('tests.accept_serial_synthesis_research.mounted_images', return_value=set()):
            with image_lease(self.image, self.folder):
                self.assertTrue(self.folder.is_dir())

    def test_existing_mount_blocks_before_the_body_is_entered(self):
        entered = False
        with patch('tests.accept_serial_synthesis_research.mounted_images', return_value={self.image.resolve()}):
            with self.assertRaises(ContractError):
                with image_lease(self.image, self.folder):
                    entered = True
        self.assertFalse(entered)

    def test_an_unrelated_image_is_not_a_global_app_lock(self):
        with patch('tests.accept_serial_synthesis_research.mounted_images', return_value={self.root / 'other.dmg'}):
            with image_lease(self.image, self.folder):
                pass

    def test_two_collectors_cannot_hold_the_same_image_lease(self):
        with patch('tests.accept_serial_synthesis_research.mounted_images', return_value=set()):
            with image_lease(self.image, self.folder):
                with self.assertRaises(ContractError):
                    with image_lease(self.image, self.folder):
                        self.fail('Concurrent collector entered')

    def test_exception_releases_os_lock_without_unlinking_the_inode(self):
        with patch('tests.accept_serial_synthesis_research.mounted_images', return_value=set()):
            with self.assertRaises(RuntimeError):
                with image_lease(self.image, self.folder):
                    raise RuntimeError('fixture')
            inode = (self.folder / '.researchhelper-service.lock').stat().st_ino
            with image_lease(self.image, self.folder):
                self.assertEqual((self.folder / '.researchhelper-service.lock').stat().st_ino, inode)

    def test_local_startup_error_is_recorded_but_not_retried(self):
        runtime = DiagnosticRuntime(self.root, {})
        error = subprocess.CalledProcessError(1, ['/usr/bin/hdiutil', 'attach'], stderr=b'fixture: already attached')
        with patch('tests.accept_one_sentence_research.OneSentenceRuntime.start', side_effect=error) as start:
            with self.assertRaises(subprocess.CalledProcessError):
                runtime.start()
        start.assert_called_once()
        self.assertEqual(runtime.record['local_startup_failure'],
            dict(program='hdiutil', returncode=1, local_diagnostic='fixture: already attached'))

    def cleanup_runtime(self):
        runtime = DiagnosticRuntime(self.root, {'service_stopped': True})
        runtime.mount = self.root / 'mount-owned'
        runtime.mount.mkdir()
        runtime.attached = True
        runtime.process = Mock()
        runtime.process.poll.return_value = 0
        runtime.mount_folder = Mock()
        error = subprocess.CalledProcessError(1, ['/usr/bin/hdiutil', 'detach', str(runtime.mount)])
        return runtime, error

    def test_detach_retry_requires_closed_service_and_no_open_files(self):
        runtime, error = self.cleanup_runtime()
        with patch('tests.accept_one_sentence_research.OneSentenceRuntime.stop', side_effect=error), \
                patch('tests.accept_serial_synthesis_research.subprocess.run', side_effect=[
                    subprocess.CompletedProcess([], 1, stdout=b'', stderr=b''),
                    subprocess.CompletedProcess([], 0, stdout=b'', stderr=b'')]) as run:
            runtime.stop()
        self.assertEqual(run.call_args_list[-1].args[0], ['/usr/bin/hdiutil', 'detach', str(runtime.mount)])
        self.assertFalse(runtime.attached)
        self.assertTrue(runtime.record['mount_detached'])
        runtime.mount_folder.cleanup.assert_called_once()

    def test_open_image_is_preserved_without_forced_detach_or_recursive_removal(self):
        runtime, error = self.cleanup_runtime()
        with patch('tests.accept_one_sentence_research.OneSentenceRuntime.stop', side_effect=error), \
                patch('tests.accept_serial_synthesis_research.subprocess.run', return_value=
                    subprocess.CompletedProcess([], 0, stdout=b'fixture_open_handle', stderr=b'')) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                runtime.stop()
        self.assertEqual(run.call_count, 1)
        runtime.mount_folder.cleanup.assert_not_called()
        runtime.mount_folder._finalizer.detach.assert_called_once()
        self.assertTrue(runtime.record['owned_mount_preserved'])

    def test_live_service_is_not_stopped_or_detached_by_recovery(self):
        runtime, error = self.cleanup_runtime()
        runtime.process.poll.return_value = None
        with patch('tests.accept_one_sentence_research.OneSentenceRuntime.stop', side_effect=error), \
                patch('tests.accept_serial_synthesis_research.subprocess.run') as run:
            with self.assertRaises(subprocess.CalledProcessError):
                runtime.stop()
        run.assert_not_called()
        runtime.mount_folder.cleanup.assert_not_called()
