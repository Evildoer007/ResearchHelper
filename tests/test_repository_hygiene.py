"""Current repository boundaries, independent of the retired layout tests."""
from pathlib import Path
import re,sys,unittest
from tests import ROOT

class RepositoryHygieneTests(unittest.TestCase):
    def test_runtime_sources_remain_complete(self):
        sys.path.insert(0,str(ROOT/'packaging'))
        from source_snapshot import source_files
        self.assertGreater(len(source_files(ROOT)),100)
    def test_retired_qt_and_migration_trees_are_absent(self):
        for name in ['legacy','historical-tests','historical-build-tools']:
            self.assertFalse((ROOT/'maintenance'/name).exists(),name)
    def test_current_docs_do_not_accumulate_dated_patch_notes(self):
        dated=[p.name for p in (ROOT/'docs').glob('*.md') if re.search(r'(?:_\d{8}|through-\d{8}-\d{6})',p.name)]
        self.assertEqual(dated,[])
    def test_skill_guides_are_bounded_and_tools_are_authoritative(self):
        for name in ['runtime','evidence','delivery','knowledge']:
            self.assertLess((ROOT/'references'/(name+'.md')).stat().st_size,6000,name)
        text=(ROOT/'references/runtime.md').read_text()
        self.assertIn('session.tools(task_id)',text);self.assertIn('defer_delivery=True',text)
    def test_required_delivery_helpers_still_exist(self):
        for name in ['candidate_acceptance.py','compact_acceptance.py','tidy_development_workspace.py']:
            self.assertTrue((ROOT/'maintenance'/name).is_file())
    def test_no_runtime_or_packages_point_into_retired_source(self):
        files=[p for folder in ['core/src','modules','products','packaging'] for p in (ROOT/folder).rglob('*.py') if 'tests' not in p.parts]
        for file in files:
            text=file.read_text()
            self.assertFalse(any(x in text for x in ['maintenance/legacy/','maintenance/historical-tests/']),str(file.relative_to(ROOT)))

if __name__=='__main__':unittest.main()
