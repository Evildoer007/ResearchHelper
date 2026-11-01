"""Generated demo uses the same active inventory as the App bootstrap."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.evidence import active_source_capabilities


ROOT = Path(__file__).resolve().parents[2]


class GeneratedSourceScopeTests(unittest.TestCase):
    def test_generated_demo_excludes_wind_and_keeps_all_eight_cases(self):
        spec = importlib.util.spec_from_file_location("isolated_demo_generator", ROOT / "packaging/assemble_release.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        base = ROOT / "result/acceptance/analysis-checkpoint-structure"
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="generated-scope-", dir=base) as output, \
                patch.object(generator, "OUTPUT", Path(output)), \
                patch("socket.socket.connect", side_effect=AssertionError("External network forbidden")):
            generator.demo()
            source = (Path(output) / "demo/public/demo-data.js").read_text()
            payload = json.loads(source.removeprefix("window.RH_DEMO = ").removesuffix(";"))
            self.assertEqual(payload["capabilities"], active_source_capabilities())
            self.assertNotIn("wind", payload["capabilities"])
            self.assertEqual(len(payload["tasks"]), 8)
            self.assertTrue(all(task["status"] == "已完成" for task in payload["tasks"]))

