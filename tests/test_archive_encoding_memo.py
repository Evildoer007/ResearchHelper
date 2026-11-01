"""Transaction-local encoding reuse preserves byte identity and tamper checks."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper import archive_codec as codec
from researchhelper.contracts import ContractError


class ArchiveEncodingMemoTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE archive_values(id TEXT PRIMARY KEY,byte_count INTEGER NOT NULL,compressed BLOB NOT NULL)')
        self.snapshot=dict(text='独立大快照夹具，不是实际研究或来源批准。'*800)
        self.value=dict(sources=[dict(snapshot=copy.deepcopy(self.snapshot)) for _ in range(15)])

    def test_distinct_objects_with_same_bytes_are_stored_and_verified_once(self):
        self.db.execute('BEGIN IMMEDIATE')
        with patch.object(codec,'_get',wraps=codec._get) as read:
            body=codec.encode_body(self.db,self.value)
            self.assertEqual(read.call_count,0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM archive_values').fetchone()[0],1)
        self.assertEqual(codec.decode_body(self.db,body),self.value)

    def test_warm_write_checks_existing_value_once_per_encode_not_once_forever(self):
        self.db.execute('BEGIN IMMEDIATE')
        codec.encode_body(self.db,self.value)
        with patch.object(codec,'_get',wraps=codec._get) as read:
            first=codec.encode_body(self.db,self.value)
            self.assertEqual(read.call_count,1)
            second=codec.encode_body(self.db,self.value)
            self.assertEqual(read.call_count,2)
        self.assertEqual(first,second)

    def test_tampering_between_encodes_is_not_hidden_by_a_process_cache(self):
        self.db.execute('BEGIN IMMEDIATE')
        codec.encode_body(self.db,self.value)
        self.db.execute('UPDATE archive_values SET compressed=?',(b'broken',))
        with self.assertRaises(ContractError):
            codec.encode_body(self.db,self.value)

    def test_memo_limit_changes_only_cost_not_encoded_or_decoded_value(self):
        self.db.execute('BEGIN IMMEDIATE')
        normal=codec.encode_body(self.db,self.value)
        with patch.object(codec,'MAX_MEMO_BYTES',1),patch.object(codec,'_get',wraps=codec._get) as read:
            bounded=codec.encode_body(self.db,self.value)
            self.assertEqual(read.call_count,15)
        self.assertEqual(normal,bounded)
        self.assertEqual(codec.decode_body(self.db,bounded),self.value)

    def test_mutated_object_is_hashed_again_not_reused_by_identity(self):
        self.db.execute('BEGIN IMMEDIATE')
        codec.encode_body(self.db,self.value)
        self.value['sources'][0]['snapshot']['text']+='已改变'
        body=codec.encode_body(self.db,self.value)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM archive_values').fetchone()[0],2)
        self.assertEqual(codec.decode_body(self.db,body),self.value)

    def test_collision_with_different_canonical_bytes_is_rejected(self):
        class SameHash:
            def hexdigest(self):return 'a'*64
        self.db.execute('BEGIN IMMEDIATE')
        value=dict(sources=[dict(snapshot=self.snapshot),dict(snapshot=dict(text=self.snapshot['text']+'不同'))])
        with patch.object(codec.hashlib,'sha256',return_value=SameHash()),self.assertRaises(ContractError):
            codec.encode_body(self.db,value)
