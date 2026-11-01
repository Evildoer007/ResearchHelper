"""Current Desk interaction over the unchanged package regression suite.

The old mobile inline-composer color check describes a retired layout. The
replacement checks unobstructed reading, the same conversation and the visible
floating panel's actual semantic surface. The report menu checks the last enabled action instead of fixing one action
to that position. All other baseline checks remain.
"""
from tests import accept_visual_upgrade_workspace as previous
from tests import accept_resdesk_completion as completion
original_fixture=completion.fixture

OLD='''      if(width===390)check(theme+' mobile Desk composer follows semantic ground',await page.locator('.assistant-region').evaluate(el=>{
        const probe=document.createElement('span');probe.style.backgroundColor='var(--color-ground)';el.append(probe);
        const expected=getComputedStyle(probe).backgroundColor,actual=getComputedStyle(el).backgroundColor;
        probe.remove();return actual===expected;
      }));'''
NEW='''      if(width===390){
        check(theme+' mobile Desk reserves no input overlay while reading',await page.locator('.assistant-region').evaluate(el=>el.getBoundingClientRect().height===0)&&await page.locator('#assistant-panel').getAttribute('aria-hidden')==='true');
        await page.locator('[data-action=assistant-toggle]').click();
        check(theme+' mobile Desk opens the same accessible composer',await page.locator('#chat-input').isVisible()&&await page.locator('#chat-input').evaluate(el=>el===document.activeElement));
        check(theme+' floating conversation uses shared theme material',await page.locator('#assistant-panel').evaluate(el=>{
          const probe=document.createElement('span');probe.style.backgroundColor='var(--desk-assistant-fill)';el.append(probe);
          const expected=getComputedStyle(probe).backgroundColor,actual=getComputedStyle(el).backgroundColor;
          probe.remove();return actual===expected&&getComputedStyle(el).backdropFilter.includes('blur(24px)');
        }));
        await page.keyboard.press('Escape');
        check(theme+' mobile conversation closes and restores its trigger',await page.locator('#assistant-panel').getAttribute('aria-hidden')==='true'&&await page.locator('[data-action=assistant-toggle]').evaluate(el=>el===document.activeElement));
      }'''

OLD_MENU="""  check('report menu End reaches version action',await page.locator('#report-more-menu [data-action=versions]').evaluate(el=>el===document.activeElement));"""
NEW_MENU="""  const reportMenuItems=page.locator('#report-more-menu [role=menuitem]:not(:disabled)');
  check('report menu End reaches its last enabled action',await reportMenuItems.last().evaluate(el=>el===document.activeElement));
  check('report versions remain available in the menu',await page.locator('#report-more-menu [data-action=versions]').isVisible()&&await page.locator('#report-more-menu [data-action=versions]').isEnabled());
  check('report correction remains available in the menu',await page.locator('#report-more-menu [data-action=report-corrections]').isVisible()&&await page.locator('#report-more-menu [data-action=report-corrections]').isEnabled());"""


def fixture(output):
    target,digest=original_fixture(output);text=target.read_text()
    if text.count(OLD)!=1:raise RuntimeError('Legacy inline-composer assertion changed; inspect before adapting')
    target.write_text(text.replace(OLD,NEW))
    return target,digest


def run():
    baseline=completion.notebook.baseline
    original_checks=baseline.EXTRA_CHECKS
    if original_checks.count(OLD_MENU)!=1:raise RuntimeError('Report menu keyboard assertion changed; inspect before adapting')
    baseline.EXTRA_CHECKS=original_checks.replace(OLD_MENU,NEW_MENU)
    completion.fixture=fixture
    try:previous.run()
    finally:
        completion.fixture=original_fixture
        baseline.EXTRA_CHECKS=original_checks

if __name__=='__main__':run()
