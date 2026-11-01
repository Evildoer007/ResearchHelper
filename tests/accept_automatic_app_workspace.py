"""Extend the unchanged App suite for a point-and-stem comparison encoding.

The old suite assumes a chart has exactly one bar series containing object
points. Auto selection may use a numeric stem series plus a scatter series.
Check the actual bound values, selector and ledger without weakening source,
account, keyboard, task, export or print checks.
"""
import tests.accept_client_workspace as base

_original_fixture=base.browser_fixture


def fixture(output):
    path,original_hash=_original_fixture(output)
    text=path.read_text()
    previous="""const chart=echarts.getInstanceByDom(el),values=chart.getOption().series.flatMap(series=>series.data.map(point=>point.value));
        return values.length===2&&values[0]===.2&&values[1]===.6&&el.getBoundingClientRect().width>0;"""
    replacement="""const chart=echarts.getInstanceByDom(el),option=chart.getOption();
        const stems=option.series.find(series=>series.type==='bar');
        const values=stems.data.map(point=>typeof point==='number'?point:point.value);
        const context=RHVisuals.contexts().find(row=>row.target===el.id);
        return values.length===2&&values[0]===.2&&values[1]===.6&&context.scheme==='lollipop'&&
          el.closest('figure').querySelector('.evidence-ledger')!==null&&el.getBoundingClientRect().width>0;"""
    startup="  const settings=async()=>{"
    assert text.count(startup)==1
    text=text.replace(startup,startup+"""
    await page.locator('[data-workspace-shell][data-layout-ready="true"][data-initializing="false"]').waitFor();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
""")
    assert text.count(previous)==1,'The original chart assertion changed; review before extending it'
    path.write_text(text.replace(previous,replacement))
    return path,original_hash


def run():
    base.browser_fixture=fixture
    try:return base.run()
    finally:base.browser_fixture=_original_fixture


if __name__=='__main__':run()
