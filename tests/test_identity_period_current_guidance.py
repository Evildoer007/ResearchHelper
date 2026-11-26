"""Compatibility entry for the historical structured-financial test runner."""
from importlib import import_module


def load_tests(loader, _tests, _pattern):
    name = 'tests.test_identity_period_provenance' if __package__ else 'test_identity_period_provenance'
    return loader.loadTestsFromTestCase(import_module(name).IdentityPeriodProvenanceTests)
