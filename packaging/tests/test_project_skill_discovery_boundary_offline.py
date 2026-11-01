"""Generated Skill installation is local; maintained Skill source stays visible."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


def ignored(path):
    result = subprocess.run(["git", "check-ignore", "--no-index", path], cwd=ROOT,
                            capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr)
    return result.returncode == 0


class ProjectSkillDiscoveryBoundary(unittest.TestCase):
    def test_generated_installation_and_discovery_are_local(self):
        self.assertTrue(ignored(".skills/researchhelper/manifest.json"))
        self.assertTrue(ignored(".agents/skills/researchhelper"))

    def test_source_metadata_is_not_hidden(self):
        self.assertFalse(ignored("agents/openai.yaml"))
        self.assertFalse(ignored("SKILL.md"))

    def test_other_maintained_skills_are_not_blanket_ignored(self):
        self.assertFalse(ignored(".agents/skills/another-skill/SKILL.md"))

    def test_discovery_still_resolves_to_the_protected_installation(self):
        discovery = ROOT / ".agents/skills/researchhelper"
        self.assertTrue(discovery.is_symlink())
        self.assertEqual(discovery.resolve(), (ROOT / ".skills/researchhelper").resolve())


if __name__ == "__main__":
    unittest.main()
