"""Compatibility entry for the historical acquisition-navigation test runner."""
if __package__:
    from .test_identity_period_current_guidance import load_tests
else:
    from test_identity_period_current_guidance import load_tests
