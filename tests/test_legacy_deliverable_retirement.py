"""Independent safety tests. Synthetic migration proof is patched only here."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from maintenance import retire_legacy_deliverables as policy

ROOT=Path(__file__).resolve().parents[1]


class LegacyDeliverableRetirementTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='legacy-delivery-policy-',dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.project=Path(folder.name)/'project';self.project.mkdir()
        self.recovery=Path(folder.name)/'recovery';self.recovery.mkdir()
        self.candidate=self.project/'result/build-candidates/fixture/candidate.json'
        self.current=dict(artifacts={'dmg':{'path':str(self.project/'versions/app/v0.10.0/current/ResearchHelper.dmg')}},
                          directory_artifacts={'demo':{'path':str(self.project/'result/build-candidates/fixture/demo')}})
        self.moves=[]
        for name,source in policy.TARGETS.items():
            path=self.project/name
            payload={'icon_16x16.png':b'synthetic-icon'} if path.suffix=='.iconset' else (
                {'':b'synthetic-icns'} if path.suffix=='.icns' else {'SKILL.md':b'old generated Skill','manifest.json':b'{}'})
            for relative,content in payload.items():
                file=path/relative if relative else path
                file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(content)
            self.moves.append(dict(source=source,target=name,
                files={key:hashlib.sha256(value).hexdigest() for key,value in payload.items()}))
        proof=self.project/policy.AUTHORITY;proof.mkdir(parents=True)
        values={'moves.json':self.moves,
                'relocation.json':dict(moves=len(self.moves),status='relocated_not_yet_accepted',optionHelperModified=False),
                'acceptance.json':dict(status='scoped_passed')}
        hashes={}
        for name,value in values.items():
            file=proof/name;file.write_text(json.dumps(value));hashes[name]=policy.digest(file)
        self.addCleanup(patch.stopall)
        patch.object(policy,'AUTHORITY_HASHES',hashes).start()
        patch.object(policy,'verify_current_build',return_value=self.current).start()
        patch.object(policy,'ensure_idle').start()
        self.icons=self.project/'versions/app/v0.10.0/ResearchHelper.iconset'
        self.skill=self.project/'versions/skill/v0.10.0/researchhelper'

    def inventory(self): return policy.inventory(self.candidate,self.project)
    def retire(self): return policy.retire(self.candidate,self.project,self.recovery)

    def test_exact_migration_copies_are_eligible(self):
        plan=self.inventory();self.assertEqual(len(plan['targets']),3);self.assertFalse(plan['skipped'])

    def test_changed_bytes_remain(self):
        (self.skill/'SKILL.md').write_text('user edit')
        self.assertIn(str(self.skill),[item['source'] for item in self.inventory()['skipped']])

    def test_extra_private_file_remains(self):
        (self.skill/'model-settings.json').write_text('private fixture')
        self.assertEqual(len(self.inventory()['skipped']),1)
        self.retire();self.assertTrue((self.skill/'model-settings.json').exists())

    def test_extra_empty_directory_remains(self):
        (self.skill/'private-empty').mkdir();self.assertEqual(len(self.inventory()['skipped']),1)

    def test_missing_recorded_file_remains(self):
        (self.skill/'manifest.json').rename(self.skill/'changed.json')
        self.assertEqual(len(self.inventory()['skipped']),1)

    def test_file_link_remains(self):
        original=self.icons/'icon_16x16.png';original.rename(self.icons/'actual.png');original.symlink_to('actual.png')
        self.assertEqual(len(self.inventory()['skipped']),1)

    def test_target_link_remains(self):
        saved=self.icons.with_name('saved-icons');self.icons.rename(saved);self.icons.symlink_to(saved,target_is_directory=True)
        self.assertEqual(len(self.inventory()['skipped']),1);self.assertTrue(saved.is_dir())

    def test_modified_historical_authority_stops(self):
        (self.project/policy.AUTHORITY/'moves.json').write_text('[]')
        with self.assertRaises(RuntimeError): self.retire()
        self.assertTrue(self.icons.is_dir());self.assertTrue(self.skill.is_dir())

    def test_current_artifact_overlap_stops(self):
        self.current['artifacts']['dmg']['path']=str(self.icons/'icon_16x16.png')
        with self.assertRaises(RuntimeError): self.retire()
        self.assertTrue(self.icons.is_dir())

    def test_tracked_source_is_not_retired(self):
        value=b'versions/skill/v0.10.0/researchhelper/SKILL.md\0'
        with patch.object(policy.subprocess,'run',return_value=SimpleNamespace(stdout=value)):
            plan=self.inventory()
        self.assertEqual(len(plan['targets']),2)
        self.assertEqual(plan['skipped'][0]['source'],str(self.skill))

    def test_recoverable_transfer_has_complete_before_after_proof(self):
        result=self.retire();record=json.loads(Path(result['receipt']).read_text())
        self.assertEqual(record['status'],'retired_recoverably');self.assertEqual(record['files_moved'],4)
        self.assertFalse(record['permanent_deletion']);self.assertTrue(record['current_candidate_verified'])
        for item in record['moves']:
            self.assertFalse(Path(item['source']).exists());self.assertTrue(Path(item['destination']).exists())
            self.assertTrue(item['content_verified'])
        self.assertEqual(self.retire()['status'],'nothing_to_retire')

    def test_file_changed_after_inventory_does_not_move(self):
        calls=[]
        def mutate(path):
            calls.append(path)
            if len(calls)==1: (self.icons/'icon_16x16.png').write_bytes(b'new user edit')
        with patch.object(policy,'ensure_idle',side_effect=mutate):
            with self.assertRaises(RuntimeError): self.retire()
        self.assertTrue(self.icons.is_dir());self.assertTrue(self.skill.is_dir())

    def test_unavailable_recovery_directory_stops(self):
        with self.assertRaises(RuntimeError): policy.retire(self.candidate,self.project,self.recovery/'missing')
        self.assertTrue(self.icons.is_dir())

    def test_symlinked_recovery_stops(self):
        link=self.recovery.with_name('recovery-link');link.symlink_to(self.recovery,target_is_directory=True)
        with self.assertRaises(RuntimeError): policy.retire(self.candidate,self.project,link)
        self.assertTrue(self.icons.is_dir())

    def test_other_project_root_is_rejected_before_mutation(self):
        with self.assertRaises(RuntimeError): policy.inventory(self.candidate,Path('/Users/haoranxu/Desktop/OptionHelper'))


if __name__=='__main__': unittest.main()
