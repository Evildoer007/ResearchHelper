"""Frozen-code replay with host-reparsed PDF; the reader itself is delivered code.

The PYZ inspection loader is not a PyInstaller executable and cannot spawn a
frozen PDF worker. Verify that separate boundary on the host, then bind exact
bytes and parsed parts here. This does not claim a frozen PDF parser rerun.
"""
import base64
import hashlib
import importlib
import json
import os
from unittest.mock import patch
from researchhelper.contracts import fingerprint
_prefix=__package__+'.' if __package__ else ''
_original=importlib.import_module(_prefix+'test_retained_mixed_financial_extraction')

class DeliveredRetainedMixedExtractionTests(_original.RetainedMixedExtractionTests):
    def test_real_retained_api_and_long_original_both_reach_the_reader(self):
        verified=json.loads(os.environ['RESEARCHHELPER_RETAINED_PDF_VERIFIED'])
        def original_boundary(record):
            material=record['snapshot']['material'] if record['kind']=='filing' else record['snapshot']
            self.assertEqual(hashlib.sha256(base64.b64decode(material['original_base64'],validate=True)).hexdigest(),material['hash'])
            self.assertEqual(fingerprint(dict(hash=material['hash'],parts=material['parts'],
                extract_version=material['extract_version'])),verified[record['id']])
        with patch('researchhelper.source_reader._verify_original_text',side_effect=original_boundary):
            super().test_real_retained_api_and_long_original_both_reach_the_reader()
