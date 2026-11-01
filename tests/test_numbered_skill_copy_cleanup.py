"""New independent arbitrary-ordinal copy checks; old cleanup tests stay intact."""
import json
from pathlib import Path
import unittest

from tests import test_empty_skill_duplicate_cleanup_offline as original
from maintenance.retire_empty_skill_duplicates import inventory, apply_inventory, digest


class NumberedSkillCopyTests(original.EmptySkillDuplicateTests):
    def test_later_ordinals_use_same_exact_content_rule(self):
        for name in ('SKILL 3.md', 'SKILL 11.md'):
            (self.target / name).write_bytes((self.target / 'SKILL.md').read_bytes())
        plan = inventory(self.root)
        self.assertEqual(len(plan['copies']), 4)
        result = apply_inventory(plan, root=self.root, recovery=self.directory / 'recovery')
        self.assertEqual(result['copies'], 4)
        self.assertTrue((Path(result['recovery']) / 'SKILL 11.md').is_file())

    def test_edited_later_copy_is_not_moved(self):
        (self.target / 'SKILL 3.md').write_text('independent user changes')
        with self.assertRaises(RuntimeError):
            inventory(self.root)
        self.assertEqual((self.target / 'SKILL 3.md').read_text(), 'independent user changes')

    def test_unknown_extension_is_not_a_generated_copy(self):
        (self.target / 'README 3.json').write_text('user file')
        with self.assertRaises(RuntimeError):
            inventory(self.root)
        self.assertTrue((self.target / 'README 3.json').exists())

    def old_manifest(self, recorded):
        value = json.loads((self.target / 'manifest.json').read_text())
        value['previous_generated_version'] = True
        copy = self.target / 'manifest 3.json'
        copy.write_text(json.dumps(value))
        if recorded:
            folder = self.root / 'result/build-candidates/old'
            folder.mkdir(parents=True)
            source = folder / 'skill-manifest.json'
            source.write_bytes(copy.read_bytes())
            release = folder / 'release-manifest.json'
            release.write_text(json.dumps({'component_manifests': {'skill': {
                'path': str(source.resolve()), 'sha256': digest(source)}}}))
            (folder / 'candidate.json').write_text(json.dumps({
                'schema': 'researchhelper.current-build.v1', 'manifest_sha256': digest(release)}))
        return copy

    def test_old_generated_manifest_requires_retained_authority(self):
        copy = self.old_manifest(recorded=False)
        with self.assertRaises(RuntimeError):
            inventory(self.root)
        self.assertTrue(copy.exists())

    def test_old_recorded_manifest_is_retired_without_its_dmg(self):
        copy = self.old_manifest(recorded=True)
        expected = digest(copy)
        plan = inventory(self.root)
        result = apply_inventory(plan, root=self.root, recovery=self.directory / 'recovery')
        self.assertEqual(digest(Path(result['recovery']) / copy.name), expected)

    def test_modified_retained_authority_does_not_enable_cleanup(self):
        copy = self.old_manifest(recorded=True)
        (self.root / 'result/build-candidates/old/skill-manifest.json').write_text('{}')
        with self.assertRaises(RuntimeError):
            inventory(self.root)
        self.assertTrue(copy.exists())


if __name__ == '__main__':
    unittest.main()
