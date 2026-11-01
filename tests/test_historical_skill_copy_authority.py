"""Old numbered Skill copies require exact recorded release provenance."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from maintenance.retire_empty_skill_duplicates import inventory, apply_inventory


class HistoricalSkillCopyAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='rh-historical-skill-copy-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.root = self.directory / 'project'
        self.target = self.root / '.skills/researchhelper'
        self.target.mkdir(parents=True)
        self.files = ['SKILL.md', 'README.md', 'requirements.lock']
        current = {name: ('Current '+name).encode() for name in self.files}
        older = {name: ('Older controlled '+name).encode() for name in self.files}
        for name in self.files:
            (self.target / name).write_bytes(current[name])
            stem, suffix = name.rsplit('.', 1)
            (self.target / (stem+' 2.'+suffix)).write_bytes(older[name])

        def manifest(files):
            return dict(version='0.10.0', project_level=True, offline_default=True, starts_second_model=False,
                runtime=dict(protocol_id='researchhelper.module-request'),
                files={name:hashlib.sha256(value).hexdigest() for name,value in files.items()})

        (self.target / 'manifest.json').write_text(json.dumps(manifest(current)))
        old_bytes = json.dumps(manifest(older)).encode()
        (self.target / 'manifest 2.json').write_bytes(old_bytes)
        candidate = self.root / 'result/build-candidates/controlled-old'
        candidate.mkdir(parents=True)
        self.old_manifest = candidate / 'skill-manifest.json'
        self.old_manifest.write_bytes(old_bytes)
        release = dict(component_manifests=dict(skill=dict(path=str(self.old_manifest),
            sha256=hashlib.sha256(old_bytes).hexdigest())))
        self.release = candidate / 'release-manifest.json'
        self.release.write_text(json.dumps(release))
        (candidate / 'candidate.json').write_text(json.dumps(dict(schema='researchhelper.current-build.v1',
            manifest_sha256=hashlib.sha256(self.release.read_bytes()).hexdigest())))

    def test_known_old_full_set_can_be_retired_without_changing_current_payload(self):
        before = {name:(self.target/name).read_bytes() for name in [*self.files,'manifest.json']}
        plan = inventory(self.root)
        self.assertEqual(len(plan['copies']), 4)
        result = apply_inventory(plan, root=self.root, recovery=self.directory/'recovery')
        self.assertEqual(result['status'], 'retired_recoverably')
        self.assertEqual({name:(self.target/name).read_bytes() for name in before}, before)
        self.assertTrue((Path(result['recovery'])/'SKILL 2.md').is_file())

    def test_unknown_modified_old_file_is_preserved(self):
        (self.target/'SKILL 2.md').write_text('User modification')
        with self.assertRaises(RuntimeError): inventory(self.root)
        self.assertTrue((self.target/'SKILL 2.md').is_file())

    def test_missing_paired_manifest_does_not_accept_loose_old_hash(self):
        (self.target/'manifest 2.json').unlink()
        with self.assertRaises(RuntimeError): inventory(self.root)

    def test_same_content_manifest_with_no_recorded_release_is_not_authority(self):
        self.old_manifest.write_text('Unrelated changed record')
        with self.assertRaises(RuntimeError): inventory(self.root)

    def test_changed_release_binding_preserves_numbered_set(self):
        self.release.write_text('{}')
        with self.assertRaises(RuntimeError): inventory(self.root)


if __name__ == '__main__': unittest.main()
