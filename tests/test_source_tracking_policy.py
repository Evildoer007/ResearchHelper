"""Source handover must include every package input and exclude local secrets."""
from pathlib import Path
import subprocess,sys,unittest
from tests import ROOT
sys.path.insert(0,str(ROOT/'packaging'))
from source_snapshot import source_files


def ignored(paths):
    done=subprocess.run(['/usr/bin/git','check-ignore','--no-index','--stdin','-z'],cwd=ROOT,
        input=('\0'.join(paths)+'\0').encode(),capture_output=True,check=False)
    if done.returncode not in (0,1):raise AssertionError('Git ignore inspection failed')
    return {name for name in done.stdout.decode().split('\0') if name}


class SourceTrackingPolicyTests(unittest.TestCase):
    def test_every_first_party_release_source_is_trackable(self):
        paths=[str(path.relative_to(ROOT)) for path in source_files(ROOT)]
        self.assertFalse(ignored(paths),'A clean checkout would silently omit required release source')

    def test_source_exception_does_not_expose_credentials_or_local_research(self):
        private=['.env','config.local.json','private.key','credentials/model.secret',
            'source-credentials.json','data/credentials/sample.secret','data/research.sqlite3',
            'result/study.json','.researchhelper/credentials/sample.secret','.skills/researchhelper/local.json']
        self.assertEqual(ignored(private),set(private))
        self.assertFalse(ignored(['core/src/researchhelper/credentials.py']))


if __name__=='__main__':unittest.main()
