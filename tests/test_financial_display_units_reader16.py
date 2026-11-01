"""Retain all four incumbent checks, adapting only their pinned reader marker.

The original file is not edited. Exact-source replacement is intentionally
limited to its one obsolete version string; every financial/freeze assertion
remains the original test code.
"""
import hashlib
import inspect
from pathlib import Path
import textwrap

try:
    from . import test_financial_display_units as original
except ImportError:
    import test_financial_display_units as original

ORIGINAL_TEST_SHA256 = hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()
method = original.FinancialDisplayUnitTests.test_real_completed_research_remains_frozen_when_presented_in_billions
source = textwrap.dedent(inspect.getsource(method))
old = "data-reader-version='rh-reader-15'"
new = "data-reader-version='rh-reader-16'"
assert source.count(old) == 1 and new not in source
namespace = dict(vars(original))
exec(compile(source.replace(old, new), __file__, 'exec'), namespace)


class FinancialDisplayReader16Tests(original.FinancialDisplayUnitTests):
    test_real_completed_research_remains_frozen_when_presented_in_billions = namespace[method.__name__]
