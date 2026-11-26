"""Retain the independent reader17 regression entry using the active renderer.

The shared status and freeze assertions remain ordinary inherited tests.
Version identity is checked against the report API, without rewriting test code.
"""
try:
    from . import test_report_status_presentation as original
except ImportError:
    import test_report_status_presentation as original


class ReportStatusReader17Tests(original.ReportStatusPresentationTests):
    pass
