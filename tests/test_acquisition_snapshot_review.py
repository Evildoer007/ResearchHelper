"""Different source hash roles must stay independently checked."""
import base64
import copy
import hashlib
import unittest

from tests.review_multitype_acquisition_current import verify_source_snapshot
from researchhelper.contracts import fingerprint


class SourceSnapshotReviewTests(unittest.TestCase):
    def material(self):
        payload=b'<html>SYNTHETIC_OFFICIAL_ORIGINAL</html>'
        snapshot=dict(original_base64=base64.b64encode(payload).decode(),
            hash=hashlib.sha256(payload).hexdigest(),parts=[dict(locator='synthetic',text='SYNTHETIC')])
        return dict(kind='material',snapshot=snapshot,snapshot_hash=fingerprint(snapshot),hash=snapshot['hash'])

    def test_distinct_original_and_snapshot_hashes_both_verified(self):
        source=self.material()
        self.assertNotEqual(source['hash'],source['snapshot_hash'])
        verify_source_snapshot(source)

    def test_changed_parsed_text_is_rejected(self):
        source=self.material();source['snapshot']['parts'][0]['text']='CHANGED'
        with self.assertRaises(AssertionError):verify_source_snapshot(source)

    def test_changed_original_is_rejected_even_with_new_snapshot_hash(self):
        source=self.material();source['snapshot']['original_base64']=base64.b64encode(b'CHANGED').decode()
        source['snapshot_hash']=fingerprint(source['snapshot'])
        with self.assertRaises(AssertionError):verify_source_snapshot(source)

    def test_api_snapshot_keeps_its_original_hash_contract(self):
        source=dict(kind='api',snapshot=dict(errorcode=0,tables=[]))
        source['hash']=fingerprint(source['snapshot']);verify_source_snapshot(source)
        source['snapshot']['tables'].append(dict(changed=True))
        with self.assertRaises(AssertionError):verify_source_snapshot(source)
