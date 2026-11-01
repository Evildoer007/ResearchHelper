"""Material context codec in source, independent Skill and actual frozen App."""
from tests import accept_filing_filter_runtime as acceptance
from tests import accept_model_progress_runtime as previous

MODULES = [*previous.MODULES, 'test_material_context_reference']
TEST_COUNT = previous.TEST_COUNT + 10


def run():
    acceptance.MODULES, acceptance.TEST_COUNT = MODULES, TEST_COUNT
    return acceptance.run()


def run_frozen():
    original = previous.MODULES, previous.TEST_COUNT
    try:
        previous.MODULES, previous.TEST_COUNT = MODULES, TEST_COUNT
        previous.run_frozen()
    finally:
        previous.MODULES, previous.TEST_COUNT = original


def run_red():
    """Keep the unsupported-format baseline without changing old tests."""
    import json
    from pathlib import Path
    import subprocess
    import sys
    import tempfile
    root = Path(__file__).resolve().parents[1]
    output = Path(tempfile.mkdtemp(prefix='material-context-red-', dir=root / 'result/acceptance'))
    result = subprocess.run([sys.executable,'-B','-m','unittest','tests.test_material_context_reference'],
        cwd=root,capture_output=True,text=True,timeout=30)
    (output/'tests.log').write_text(result.stdout+result.stderr)
    record = dict(status='unsupported_format_reproduced' if result.returncode else 'unexpectedly_supported',
                  model_calls=0,provider_calls=0,research_accepted=False)
    target=output/'receipt.json';target.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print(json.dumps(dict(**record,receipt=str(target)),ensure_ascii=False))


if __name__ == '__main__':
    run()
