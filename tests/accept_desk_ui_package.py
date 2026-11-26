"""Check that keyboard navigation reaches the last available report action."""
from tests import accept_visual_upgrade_workspace as previous
from tests import accept_resdesk_completion as completion
OLD_MENU="""  check('report menu End reaches version action',await page.locator('#report-more-menu [data-action=versions]').evaluate(el=>el===document.activeElement));"""
NEW_MENU="""  const reportMenuItems=page.locator('#report-more-menu [role=menuitem]:not(:disabled)');
  check('report menu End reaches its last enabled action',await reportMenuItems.last().evaluate(el=>el===document.activeElement));
  check('report versions remain available in the menu',await page.locator('#report-more-menu [data-action=versions]').isVisible()&&await page.locator('#report-more-menu [data-action=versions]').isEnabled());
  check('report correction remains available in the menu',await page.locator('#report-more-menu [data-action=report-corrections]').isVisible()&&await page.locator('#report-more-menu [data-action=report-corrections]').isEnabled());"""


def run():
    baseline=completion.notebook.baseline
    original_checks=baseline.EXTRA_CHECKS
    if original_checks.count(OLD_MENU)!=1:raise RuntimeError('Report menu keyboard assertion changed; inspect before adapting')
    baseline.EXTRA_CHECKS=original_checks.replace(OLD_MENU,NEW_MENU)
    try:previous.run()
    finally:
        baseline.EXTRA_CHECKS=original_checks

if __name__=='__main__':run()
