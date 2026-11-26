"""Retain R157 metadata assertions under their explicit original policy."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location('retained_ifind_metadata_runtime',
    Path(__file__).with_name('test_ifind_metadata_runtime.py'))
_retained = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_retained)


class LegacyIFindMetadataRuntimeTests(_retained.IFindMetadataRuntimeTests):
    def setUp(self):
        super().setUp()
        self.use_retained_metadata_policy()
