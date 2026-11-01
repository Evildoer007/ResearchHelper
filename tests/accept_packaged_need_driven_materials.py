"""Run need-driven acquisition guards against the actual frozen App payload."""
from pathlib import Path
import inspect
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import accept_packaged_filing_filters as acceptance
from tests.accept_need_driven_material_runtime import MODULES, TEST_COUNT
from tests.accept_delivered_ifind_no_data import run_packaged_tests


def frozen_material_runner():
    """Keep the old loader, but spawn the actual bundled parser executable.

    The old PYZ audit loader is an unfrozen Python host. Binary parsing needs
    the frozen process launch convention as well as the original module code.
    No parser target, extraction cache, source check or result is mocked.
    """
    source = inspect.getsource(run_packaged_tests)
    anchor = ' sys.meta_path.insert(0, FrozenResearch())'
    assert source.count(anchor) == 1
    source = source.replace(anchor, anchor + '\n import multiprocessing, multiprocessing.util\n'
        ' sys.path.insert(0, str(location.parent / "_internal"))\n'
        ' multiprocessing.set_executable(str(location))\n'
        ' multiprocessing.util._args_from_interpreter_flags = lambda: []\n'
        ' sys.frozen = True')
    namespace = dict(run_packaged_tests.__globals__)
    exec(compile(source, __file__, 'exec'), namespace)
    return namespace['run_packaged_tests']


def run():
    acceptance.MODULES, acceptance.TEST_COUNT = MODULES, TEST_COUNT
    acceptance.run_packaged_tests = frozen_material_runner()
    return acceptance.run()


if __name__ == '__main__': run()
