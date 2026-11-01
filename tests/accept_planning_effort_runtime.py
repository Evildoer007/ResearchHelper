"""Independent phase settings checks in source, Skill and frozen App."""
from tests import accept_filing_filter_runtime as acceptance
from tests import accept_material_context_runtime as previous

MODULES = [*previous.MODULES, 'test_planning_effort_settings']
TEST_COUNT = previous.TEST_COUNT + 12


def run():
    acceptance.MODULES, acceptance.TEST_COUNT = MODULES, TEST_COUNT
    return acceptance.run()


def run_frozen():
    original = previous.MODULES, previous.TEST_COUNT
    try:
        previous.MODULES, previous.TEST_COUNT = MODULES, TEST_COUNT
        previous.run_frozen()
        from tests.accept_planning_effort_view import run as view
        view()
    finally:
        previous.MODULES, previous.TEST_COUNT = original


def run_red():
    import json
    from pathlib import Path
    import subprocess
    import sys
    import tempfile
    root=Path(__file__).resolve().parents[1]
    output=Path(tempfile.mkdtemp(prefix='planning-effort-red-',dir=root/'result/acceptance'))
    completed=subprocess.run([sys.executable,'-B','-m','unittest','tests.test_planning_effort_settings'],
        cwd=root,capture_output=True,text=True,timeout=30)
    (output/'tests.log').write_text(completed.stdout+completed.stderr)
    record=dict(status='unsupported_option_reproduced' if completed.returncode else 'unexpectedly_supported',
                model_calls=0,provider_calls=0,research_accepted=False)
    target=output/'receipt.json';target.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print(json.dumps(dict(**record,receipt=str(target)),ensure_ascii=False))


if __name__=='__main__':
    run()
