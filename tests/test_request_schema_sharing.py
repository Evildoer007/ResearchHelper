"""Lossless request-schema factoring, measured on the closed R125 case.

No network, credentials, new study or replay of discarded model output.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from researchhelper.contracts import canonical
from researchhelper.output_schema import (provider_request_schema, provider_output_schema,
    original_batch_output_schema, planning_output_schema, ORIGINAL_BATCH_PROTOCOL,
    PLAN_REQUEST_PROTOCOL)
from researchhelper.planning import input_fingerprint

ROOT = Path(__file__).resolve().parents[1]
RETAINED = Path(os.environ.get('RESEARCHHELPER_COST_PROFILE_INPUT', str(
    ROOT/'result/acceptance/one-sentence-research-9cn24t68/task.json')))


def expanded(schema):
    definitions = schema.get('$defs', {})

    def resolve(node, stack=()):
        if isinstance(node, dict):
            if '$ref' in node:
                assert set(node) == {'$ref'} and node['$ref'].startswith('#/$defs/')
                name = node['$ref'].split('/')[-1]
                assert name not in stack
                return resolve(definitions[name], stack+(name,))
            return {key: resolve(value, stack) for key, value in node.items() if key != '$defs'}
        if isinstance(node, list): return [resolve(value, stack) for value in node]
        return node
    return resolve(schema)


class RequestSchemaSharingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = RETAINED.read_bytes()
        cls.original = json.loads(cls.raw)
        cls.schema = original_batch_output_schema(cls.original,
            cls.original['reasoning_state']['reading']['bundle']['views'])

    def setUp(self):
        self.task = copy.deepcopy(self.original)
        self.task['request_schema_sharing_version'] = 1
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline schema checks'))
        guard.start(); self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(), self.raw)

    def test_financial_request_expands_to_every_original_constraint(self):
        before = canonical(self.schema)
        compact = provider_request_schema(self.task, self.schema, ORIGINAL_BATCH_PROTOCOL)
        self.assertEqual(expanded(compact), provider_output_schema(self.schema))
        self.assertEqual(canonical(self.schema), before)
        # This is schema bytes, not provider-measured tokens or the whole request.
        self.assertLess(len(canonical(compact)), len(before)*0.5)

    def test_planning_request_remains_exact_with_all_tools_and_methods(self):
        schema = planning_output_schema(self.task)
        compact = provider_request_schema(self.task, schema, PLAN_REQUEST_PROTOCOL)
        self.assertEqual(expanded(compact), provider_output_schema(schema))
        self.assertLessEqual(len(canonical(compact)), len(canonical(schema)))

    def test_unknown_policy_protocol_and_legacy_task_preserve_full_transport(self):
        expected = provider_output_schema(self.schema)
        for value in (None, True, False, 0, 2, 1.0):
            task = dict(self.task, request_schema_sharing_version=value)
            self.assertEqual(provider_request_schema(task, self.schema, ORIGINAL_BATCH_PROTOCOL), expected)
        self.assertEqual(provider_request_schema(self.task, self.schema, 'foreign_protocol'), expected)

    def test_graph_is_valid_deterministic_and_only_local_references(self):
        from jsonschema import Draft202012Validator
        compact = provider_request_schema(self.task, self.schema, ORIGINAL_BATCH_PROTOCOL)
        Draft202012Validator.check_schema(compact)
        self.assertEqual(compact, provider_request_schema(self.task, copy.deepcopy(self.schema), ORIGINAL_BATCH_PROTOCOL))
        expanded(compact)  # Rejects recursion, remote refs and changed type siblings.

    def test_policy_changes_input_identity_without_mutating_the_closed_case(self):
        old = input_fingerprint(self.original)
        self.assertNotEqual(input_fingerprint(self.task), old)
        self.assertEqual(hashlib.sha256(RETAINED.read_bytes()).digest(), hashlib.sha256(self.raw).digest())
        self.assertEqual(self.original['status'], '部分完成')


if __name__ == '__main__': unittest.main()
