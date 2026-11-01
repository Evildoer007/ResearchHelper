"""Financial presentation semantics in source, Skill and actual frozen App."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests import accept_filing_filter_runtime as acceptance
from tests.accept_host_reading_plan_runtime import MODULES as PREVIOUS,TEST_COUNT as COUNT

MODULES=[*PREVIOUS,'test_financial_chart_display']
TEST_COUNT=COUNT+9


def run():
    acceptance.MODULES,acceptance.TEST_COUNT=MODULES,TEST_COUNT
    return acceptance.run()


def run_frozen():
    import json
    from tests import accept_packaged_filing_filters as packaged
    from tests.accept_packaged_need_driven_materials import frozen_material_runner
    from tests import accept_financial_chart_layout as geometry
    root=Path(__file__).resolve().parents[1]
    pattern='result/acceptance/packaged-filing-filters-*/receipt.json'
    before=set(root.glob(pattern))
    packaged.MODULES,packaged.TEST_COUNT=MODULES,TEST_COUNT
    packaged.run_packaged_tests=frozen_material_runner()
    packaged.run()
    created=set(root.glob(pattern))-before
    if len(created)!=1:raise RuntimeError('Frozen cohort did not produce one receipt')
    receipt=created.pop()
    rendered=receipt.parent/'financial-chart-preview.html'
    result=geometry.run(frozen_html=rendered)
    (receipt.parent/'financial-chart-review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    if result['status']!='scoped_passed':raise RuntimeError('Actual frozen financial-chart geometry did not pass')


if __name__=='__main__':run()
