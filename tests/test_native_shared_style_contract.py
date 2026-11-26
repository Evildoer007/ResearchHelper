"""Known startup adaptations cannot conceal unrelated shared-style changes."""
import unittest
from tests.accept_native_workbench_render import verify_shared_style

RH_STARTUP='''body.workspace-body:not(.workspace-body--initializing)::before { display: none; }

.workspace-shell[data-initializing="false"] {
  /* Reading must remain visible when WebKit suspends an inactive timeline. */
  visibility: visible;
  opacity: 1;
}

@media (prefers-reduced-motion: reduce) {
  body.workspace-body::before {
    transition: none;
  }

}
'''
OH_STARTUP='''.workspace-shell[data-initializing="false"] {
  animation: workspace-shell-reveal .18s cubic-bezier(.16, 1, .3, 1) both;
}

@keyframes workspace-shell-reveal {
  from { opacity: 0; }
  to { opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  body.workspace-body::before {
    transition: none;
  }

  .workspace-shell[data-initializing="false"] {
    animation: none;
  }
}
'''
CONTROLS='select:focus {outline:2px solid #123456}\n'


class NativeSharedStyleTests(unittest.TestCase):
    def test_expected_base_is_identical_and_adaptations_remain_explicit(self):
        record=verify_shared_style('styles.css',RH_STARTUP+CONTROLS,OH_STARTUP+CONTROLS)
        self.assertTrue(record['shared_rules_identical'])
        self.assertFalse(record['raw_files_identical'])
        self.assertEqual(len(record['startup_adaptations']),3)
        for name in ('refinement.css','theme-overrides.css','choice-controls.css'):
            record=verify_shared_style(name,CONTROLS,CONTROLS)
            self.assertTrue(record['raw_files_identical']);self.assertEqual(record['startup_adaptations'],[])

    def test_unrelated_style_change_cannot_hide_inside_the_startup_exception(self):
        for controls in ('select:focus {outline:none}\n',CONTROLS+'body {font-size:14px}\n'):
            with self.subTest(controls=controls),self.assertRaises(RuntimeError):
                verify_shared_style('styles.css',RH_STARTUP+controls,OH_STARTUP+CONTROLS)

    def test_removing_the_inactive_window_fix_fails_even_if_upstream_has_it(self):
        with self.assertRaisesRegex(RuntimeError,'startup adaptation'):
            verify_shared_style('styles.css',OH_STARTUP+CONTROLS,OH_STARTUP+CONTROLS)

    def test_only_the_exact_researchhelper_artwork_url_is_adapted(self):
        upstream='content: url("/capability/assets/icons/optionhelper-app-icon-tile-dark.svg");'
        own='content: url("../app-icon-dark.svg");'
        record=verify_shared_style('theme-overrides.css',own+CONTROLS,upstream+CONTROLS)
        self.assertTrue(record['shared_rules_identical'])
        self.assertFalse(record['raw_files_identical'])
        self.assertEqual(record['branding_adaptations'],['supplied_researchhelper_dark_tile'])
        self.assertEqual(record['startup_adaptations'],[])
        for actual in (upstream+CONTROLS,CONTROLS,own+own+CONTROLS,
                       own+CONTROLS.replace('outline:2px solid #123456','outline:none')):
            with self.subTest(actual=actual),self.assertRaises(RuntimeError):
                verify_shared_style('theme-overrides.css',actual,upstream+CONTROLS)

    def test_startup_mark_substitution_preserves_the_three_visibility_fixes(self):
        upstream='url("/app/assets/icons/optionhelper-mark.svg")'
        own='url("../brand.svg")'
        record=verify_shared_style('styles.css',RH_STARTUP+own+CONTROLS,OH_STARTUP+upstream+CONTROLS)
        self.assertTrue(record['shared_rules_identical'])
        self.assertEqual(record['branding_adaptations'],['supplied_researchhelper_mark'])
        self.assertEqual(len(record['startup_adaptations']),3)
        with self.assertRaises(RuntimeError):
            verify_shared_style('styles.css',RH_STARTUP+upstream+CONTROLS,OH_STARTUP+upstream+CONTROLS)

    def test_other_shared_files_allow_no_unreviewed_exception(self):
        with self.assertRaises(RuntimeError):
            verify_shared_style('choice-controls.css','select {outline:none}',CONTROLS)


if __name__=='__main__':unittest.main()
