"""Preserve all six status/freeze checks; adapt only the pinned reader marker."""
import hashlib
import inspect
from pathlib import Path
import textwrap

try:
    from . import test_report_status_presentation as original
except ImportError:
    import test_report_status_presentation as original

ORIGINAL_TEST_SHA256=hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()
method=original.ReportStatusPresentationTests.test_reader_does_not_change_frozen_content_or_financial_verdict
source=textwrap.dedent(inspect.getsource(method))
assert source.count('rh-reader-16')==1 and 'rh-reader-17' not in source
namespace=dict(vars(original))
exec(compile(source.replace('rh-reader-16','rh-reader-17'),__file__,'exec'),namespace)


class ReportStatusReader17Tests(original.ReportStatusPresentationTests):
    test_reader_does_not_change_frozen_content_or_financial_verdict=namespace[method.__name__]
