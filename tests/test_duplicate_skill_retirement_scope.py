"""Retirement guards and synthetic recovery; never uses the system Trash."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from maintenance import retire_duplicate_skill_archive as retirement


class DuplicateSkillRetirementScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='rh-retirement-guards-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.patch = patch.object(retirement, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.candidate = self.root / 'result/build-candidates/current/candidate.json'
        self.candidate.parent.mkdir(parents=True)
        self.candidate.write_text('{}')

    def rejected(self, previous):
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        with self.assertRaises(RuntimeError):
            retirement.retire_recorded_duplicate(previous_candidate=previous, current_candidate=self.candidate)
        self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))

    def test_relative_receipt_rejected(self):
        self.rejected(Path('result/build-candidates/current/candidate.json'))

    def test_external_receipt_rejected(self):
        self.rejected(self.root / 'elsewhere/candidate.json')

    def test_missing_receipt_rejected(self):
        self.rejected(self.root / 'result/build-candidates/missing/candidate.json')

    def test_wrong_receipt_name_rejected(self):
        self.rejected(self.candidate.with_name('manifest.json'))

    def test_linked_receipt_rejected(self):
        link = self.candidate.parent / 'other/candidate.json'
        link.parent.mkdir()
        link.symlink_to(self.candidate)
        self.rejected(link)

    def test_absent_exact_archive_does_not_create_retirement(self):
        self.rejected(self.candidate)

    def installation(self):
        target=self.root/'.skills/researchhelper';target.mkdir(parents=True)
        current_files={'SKILL.md':b'new instructions','README.md':b'readme','requirements.lock':b'locked'}
        old_files={**current_files,'SKILL.md':b'old instructions'}
        def manifest(files):
            return json.dumps({'files':{key:hashlib.sha256(value).hexdigest() for key,value in files.items()}},sort_keys=True).encode()
        raw,old_raw=manifest(current_files),manifest(old_files)
        for name,value in current_files.items():
            (target/name).write_bytes(value)
            p=Path(name);(target/(p.stem+' 2'+p.suffix)).write_bytes(old_files[name])
        (target/'manifest.json').write_bytes(raw);(target/'manifest 2.json').write_bytes(old_raw)
        previous=self.root/'result/build-candidates/previous/candidate.json';previous.parent.mkdir()
        for path,value in ((self.candidate,raw),(previous,old_raw)):
            path.write_text(json.dumps({'project_skill_installation':{'manifest_sha256':hashlib.sha256(value).hexdigest()}}))
        trash=self.root/'temporary-recovery';trash.mkdir()
        return target,previous,trash

    def test_old_instructions_are_retired_against_their_own_recorded_manifest(self):
        target,previous,trash=self.installation()
        before={p.name:p.read_bytes() for p in target.iterdir() if ' 2' not in p.name}
        result=retirement.retire_recorded_installation_duplicates(installed_candidate=self.candidate,
            duplicate_manifest_candidate=previous,trash=trash)
        self.assertEqual(result['status'],'retired_recoverably');self.assertEqual(result['files'],4)
        self.assertEqual({p.name:p.read_bytes() for p in target.iterdir()},before)
        record=json.loads(Path(result['receipt']).read_text())
        old=next(row for row in record['files'] if Path(row['source']).name=='SKILL 2.md')
        self.assertEqual(old['authority'],str(previous));self.assertEqual(Path(old['recovery']).read_bytes(),b'old instructions')

    def test_an_identical_current_copy_is_also_a_known_generated_file(self):
        target,previous,trash=self.installation();(target/'SKILL 2.md').write_bytes((target/'SKILL.md').read_bytes())
        result=retirement.retire_recorded_installation_duplicates(installed_candidate=self.candidate,
            duplicate_manifest_candidate=previous,trash=trash)
        self.assertEqual(result['files'],4);self.assertFalse((target/'SKILL 2.md').exists())

    def test_unknown_edits_or_manifest_changes_are_not_moved(self):
        target,previous,trash=self.installation()
        for file in ('SKILL 2.md','manifest 2.json','SKILL.md'):
            path=target/file;original=path.read_bytes();path.write_bytes(b'unrecorded user edit')
            before={p.name:p.read_bytes() for p in target.iterdir()}
            with self.subTest(file=file),self.assertRaises(RuntimeError):
                retirement.retire_recorded_installation_duplicates(installed_candidate=self.candidate,
                    duplicate_manifest_candidate=previous,trash=trash)
            self.assertEqual({p.name:p.read_bytes() for p in target.iterdir()},before)
            self.assertEqual(list(trash.iterdir()),[]);path.write_bytes(original)
