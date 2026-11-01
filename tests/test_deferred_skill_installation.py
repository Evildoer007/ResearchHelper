"""Candidate assembly can leave the installed project Skill unchanged."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from tests import ROOT
sys.path.insert(0,str(ROOT/'packaging'))
import build_current


class DeferredSkillInstallationTests(unittest.TestCase):
    def test_candidate_only_build_does_not_install_or_claim_installation(self):
        with patch.object(build_current,'update_project_skill') as install:
            result=build_current.project_skill_step(Path('/unused/candidate.json'),ROOT,install_skill=False)
        install.assert_not_called()
        self.assertEqual(result,dict(status='deferred_until_research_acceptance',installed=False))

    def test_normal_build_keeps_existing_explicit_installation(self):
        with patch.object(build_current,'update_project_skill',return_value={'status':'verified'}) as install:
            result=build_current.project_skill_step(Path('/unused/candidate.json'),ROOT)
        install.assert_called_once_with(Path('/unused/candidate.json'),ROOT)
        self.assertEqual(result,dict(status='verified'))

    def test_non_boolean_option_never_builds_or_installs(self):
        with patch.object(build_current,'update_project_skill') as install:
            for value in (0,1,None,'false'):
                with self.assertRaises(ValueError):
                    build_current.project_skill_step(Path('/unused/candidate.json'),ROOT,install_skill=value)
                with self.assertRaises(ValueError):build_current.build_current(ROOT,install_skill=value)
        install.assert_not_called()


if __name__=='__main__':unittest.main()
