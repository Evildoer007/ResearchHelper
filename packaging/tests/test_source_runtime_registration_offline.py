"""Unregistered runtime files fail before costly freezing or App packaging."""
import json
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from source_snapshot import source_files


class RuntimeRegistration(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name).resolve()
        (self.root / "packaging").mkdir()
        (self.root / "core/src/researchhelper").mkdir(parents=True)
        self.module = self.root / "core/src/researchhelper/credentials.py"
        self.module.write_text('"""Fixture only."""\n')

    def register(self, names):
        (self.root / "packaging/package-source-map.json").write_text(json.dumps({"files": names}))

    def test_missing_runtime_registration_fails_closed(self):
        self.register([])
        with self.assertRaisesRegex(RuntimeError, "credentials.py"):
            source_files(self.root)

    def test_explicit_runtime_registration_passes(self):
        self.register(["core/src/researchhelper/credentials.py"])
        self.assertEqual(source_files(self.root), [self.module])

    def test_another_unregistered_runtime_file_is_not_silently_omitted(self):
        self.register(["core/src/researchhelper/credentials.py"])
        (self.module.parent / "new_module.py").write_text('"""Fixture only."""\n')
        with self.assertRaisesRegex(RuntimeError, "new_module.py"):
            source_files(self.root)
