"""Managed project Skill updates are verified, recoverable and data-free."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'packaging'))


class ProjectSkillUpdateTests(unittest.TestCase):
    def setUp(self):
        from skill import install_project_skill as installer
        self.installer = installer
        temporary = tempfile.TemporaryDirectory(prefix='project-skill-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.project = self.directory / 'research-project'
        self.project.mkdir()
        self.recovery = self.directory / 'recoverable'
        self.archive = self.directory / 'ResearchHelper-Skill-0.10.0.zip'
        self.package()

    def package(self, *, malformed=None):
        payload = {'SKILL.md': b'---\nname: researchhelper\ndescription: Research independently.\n---\n',
                   'core/session.py': b'# Current entry\n', 'researchhelper/reports.py': b'READER_VERSION="rh-reader-7"\n'}
        manifest = {'version': '0.10.0', 'files': {name: hashlib.sha256(value).hexdigest() for name, value in payload.items()},
                    'runtime': {'protocol_id': 'researchhelper.module-request'}, 'project_level': True,
                    'starts_second_model': False, 'offline_default': True}
        if malformed == 'hash':
            manifest['files']['core/session.py'] = '0' * 64
        if malformed == 'private':
            payload['data/researchhelper.sqlite3'] = b'private'
            manifest['files']['data/researchhelper.sqlite3'] = hashlib.sha256(b'private').hexdigest()
        with zipfile.ZipFile(self.archive, 'w') as archive:
            for name, value in payload.items():
                archive.writestr('researchhelper/' + name, value)
            archive.writestr('researchhelper/manifest.json', json.dumps(manifest))
            if malformed == 'traversal':
                archive.writestr('researchhelper/../escape', b'bad')
            if malformed == 'duplicate':
                archive.writestr('researchhelper/SKILL.md', payload['SKILL.md'])
            if malformed == 'symlink':
                entry = zipfile.ZipInfo('researchhelper/researchhelper/link.py')
                entry.create_system = 3
                entry.external_attr = 0o120777 << 16
                archive.writestr(entry, 'outside')

    def install(self):
        return self.installer.install_project_skill(self.archive, project_root=self.project,
                expected_sha256=hashlib.sha256(self.archive.read_bytes()).hexdigest(), recovery_root=self.recovery)

    def previous(self, *, legacy=False):
        target = self.project / '.skills/researchhelper'
        target.mkdir(parents=True)
        (target / 'SKILL.md').write_text('old instructions')
        manifest = {'version': '0.10.0', 'files': {'SKILL.md': hashlib.sha256(b'old instructions').hexdigest()},
                    'project_level': True, 'starts_second_model': False, 'offline_default': True}
        if not legacy:
            manifest['runtime'] = {'protocol_id': 'researchhelper.module-request'}
        (target / 'manifest.json').write_text(json.dumps(manifest))
        return target

    def test_fresh_install_creates_project_only_discovery(self):
        record = self.install()
        target = self.project / '.skills/researchhelper'
        self.assertEqual(record['status'], 'installed')
        self.assertTrue((target / 'core/session.py').is_file())
        self.assertEqual((self.project / '.agents/skills/researchhelper').resolve(), target)
        self.assertFalse(record['global_installation_changed'])
        self.assertEqual(record['files'], 3)

    def test_windows_discovery_junction_publishes_the_single_managed_copy(self):
        target = self.previous()
        discovery = self.project / '.agents/skills/researchhelper'
        discovery.parent.mkdir(parents=True)
        if sys.platform == 'win32':
            self.installer._windows_discovery_junction(discovery, target)
            self.assertTrue(discovery.is_junction())
        else:
            calls = []

            def create_junction(source, destination):
                calls.append((Path(source), Path(destination)))
                Path(destination).symlink_to(source, target_is_directory=True)

            with patch.dict(sys.modules, {'_winapi': SimpleNamespace(CreateJunction=create_junction)}), \
                    patch.object(Path, 'is_junction', lambda path: path.is_symlink()):
                self.installer._windows_discovery_junction(discovery, target)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], target)
            self.assertFalse(calls[0][1].parent.exists())
        self.installer.check_discovery(discovery, target)
        self.assertEqual(discovery.resolve(), target)
        self.assertEqual((discovery / 'SKILL.md').read_text(), 'old instructions')

    def test_identical_package_is_a_true_noop(self):
        self.install()
        target = self.project / '.skills/researchhelper'
        before = target.stat().st_ino, (target / 'manifest.json').stat().st_mtime_ns
        record = self.install()
        self.assertEqual(record['status'], 'already_current')
        self.assertEqual(before, (target.stat().st_ino, (target / 'manifest.json').stat().st_mtime_ns))
        self.assertFalse(self.recovery.exists())

    def test_verified_old_and_legacy_installations_are_recoverable(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy), tempfile.TemporaryDirectory(dir=self.directory) as folder:
                self.project = Path(folder) / 'project'
                self.project.mkdir()
                target = self.previous(legacy=legacy)
                old_manifest = (target / 'manifest.json').read_bytes()
                record = self.install()
                backup = Path(record['recovery'])
                self.assertEqual((backup / 'manifest.json').read_bytes(), old_manifest)
                self.assertEqual((backup / 'SKILL.md').read_text(), 'old instructions')
                self.assertTrue((target / 'core/session.py').is_file())

    def test_private_project_data_and_results_never_change(self):
        originals = []
        for name in ('data/researchhelper/original.txt', 'result/researchhelper/report.html', '.researchhelper/config.json'):
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('private preserved')
            originals.append(path)
        self.install()
        self.assertTrue(all(path.read_text() == 'private preserved' for path in originals))

    def test_unknown_modified_or_symlinked_installation_is_not_overwritten(self):
        for kind in ('unknown', 'modified', 'link', 'unknown-directory'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.directory) as folder:
                self.project = Path(folder) / 'project'
                self.project.mkdir()
                target = self.previous()
                if kind == 'unknown':
                    (target / 'user-data.txt').write_text('private')
                elif kind == 'modified':
                    (target / 'SKILL.md').write_text('user modification')
                elif kind == 'link':
                    (target / 'outside').symlink_to(self.directory)
                else:
                    (target / 'user-data').mkdir()
                before = (target / 'SKILL.md').read_bytes()
                with self.assertRaises(RuntimeError):
                    self.install()
                self.assertEqual((target / 'SKILL.md').read_bytes(), before)
                self.assertFalse((target / 'core/session.py').exists())

    def test_other_discovery_and_symlinked_parents_are_not_changed(self):
        for kind in ('other-skill', 'skills-parent'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.directory) as folder:
                self.project = Path(folder) / 'project'
                self.project.mkdir()
                if kind == 'other-skill':
                    discovery = self.project / '.agents/skills/researchhelper'
                    discovery.parent.mkdir(parents=True)
                    discovery.symlink_to(self.directory)
                else:
                    (self.project / '.skills').symlink_to(self.directory)
                with self.assertRaises(RuntimeError):
                    self.install()
                self.assertFalse((self.directory / 'researchhelper').exists())

    def test_bad_archive_and_digest_fail_before_installation_writes(self):
        for mode in ('hash', 'private', 'traversal', 'duplicate', 'symlink'):
            with self.subTest(mode=mode):
                self.package(malformed=mode)
                with self.assertRaises(RuntimeError):
                    self.install()
                self.assertFalse((self.project / '.skills').exists())
        self.package()
        with self.assertRaises(RuntimeError):
            self.installer.install_project_skill(self.archive, project_root=self.project,
                expected_sha256='0' * 64, recovery_root=self.recovery)
        self.assertFalse((self.project / '.skills').exists())

    def test_promotion_failure_restores_exact_old_installation(self):
        target = self.previous()
        original_replace = self.installer.os.replace
        def fail_new(source, destination):
            if Path(destination) == target and Path(source).name.startswith('.researchhelper-install-'):
                raise OSError('simulated promotion failure')
            return original_replace(source, destination)
        with patch.object(self.installer.os, 'replace', side_effect=fail_new):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual((target / 'SKILL.md').read_text(), 'old instructions')
        self.assertFalse((target / 'core/session.py').exists())
        self.assertFalse((self.project / '.agents/skills/researchhelper').exists())

    def test_build_entry_updates_only_after_candidate_commit(self):
        import build_current
        helper = getattr(build_current, 'update_project_skill', None)
        self.assertTrue(callable(helper), 'Current build must update its managed project Skill')
        with patch.object(build_current, 'verify_current_build', side_effect=RuntimeError('candidate invalid')):
            with self.assertRaises(RuntimeError):
                helper(self.project / 'candidate.json', self.project)
        self.assertFalse((self.project / '.skills').exists())
