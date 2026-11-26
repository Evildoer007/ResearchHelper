"""Independent safety checks before one-time repository cleanup."""
import tempfile
from pathlib import Path
import unittest
from clean_development_artifacts import cleanup_plan, validate_targets


class CleanupSafetyTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name).resolve()
        self.candidate = self.root / 'result/build-staging/current/candidate.json'
        self.candidate.parent.mkdir(parents=True)
        self.candidate.write_text('{}')
        (self.root / 'result/acceptance').mkdir()

    def test_latest_candidate_and_research_evidence_are_not_selected(self):
        for name in ('live-ifind-study', 'current-report', 'real-document', 'old-browser-fixture', 'document-delivery-release'):
            (self.root / 'result/acceptance' / name).mkdir()
        (self.root / 'result/build-staging/old').mkdir()
        plan = cleanup_plan(self.root, self.candidate)
        validate_targets(plan, self.root, self.candidate)
        names = {Path(item['source']).name for item in plan}
        self.assertEqual(names, {'old', 'old-browser-fixture', 'document-delivery-release'})

    def test_new_reference_invalidates_existing_cleanup_plan(self):
        case = self.root / 'result/acceptance/old-qa-case'
        case.mkdir()
        plan = cleanup_plan(self.root, self.candidate)
        self.assertIn(str(case), {entry['source'] for entry in plan})
        tests = self.root / 'tests'
        tests.mkdir()
        (tests / 'replay.swift').write_text(f'let input = "result/acceptance/{case.name}/task.json"')
        self.assertNotIn(str(case), {entry['source'] for entry in cleanup_plan(self.root, self.candidate)})
        with self.assertRaisesRegex(RuntimeError, '仍被回归引用'):
            validate_targets(plan, self.root, self.candidate)

    def test_protected_and_ancestor_targets_are_rejected(self):
        for path in ('demo', 'data', 'dist', 'versions', '.git', 'result', 'result/host-research'):
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                validate_targets([{'source': str(self.root / path)}], self.root, self.candidate)

    def test_outside_symlink_and_mounted_targets_are_rejected(self):
        outside = self.root.parent / 'outside'; link = self.root / 'link'; link.symlink_to(outside)
        for target in (outside, link):
            with self.assertRaises(RuntimeError):
                validate_targets([{'source': str(target)}], self.root, self.candidate)
        target = self.root / 'result/build-staging/old'
        with self.assertRaises(RuntimeError):
            validate_targets([{'source': str(target)}], self.root, self.candidate, [target / 'mounted'])

    def test_overlapping_targets_are_rejected(self):
        targets = [{'source': str(self.root / 'result/old')}, {'source': str(self.root / 'result/old/child')}]
        with self.assertRaises(RuntimeError):
            validate_targets(targets, self.root, self.candidate)

    def test_unpacked_skill_and_current_release_are_protected(self):
        for name in ('researchhelper', 'demo', 'ResearchHelper.app', 'release-manifest.json'):
            path = self.candidate.parent / 'source/result/build-staging/current' / name
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                validate_targets([{'source': str(path)}], self.root, self.candidate)


if __name__ == '__main__':
    unittest.main()
