"""Compact part metadata without dropping text, nulls, geometry or addresses."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from tests import ROOT
from tests.probe_operating_extraction_capacity import payload, SOURCE
from researchhelper.contracts import canonical
from researchhelper.output_schema import (original_span_prompt, original_span_output_schema,
    provider_request_schema, ORIGINAL_SPAN_PROTOCOL, _compact_original_part_records)
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS


def restore_parts(parts):
    if isinstance(parts, list):
        return parts
    assert set(parts) == {'layouts', 'rows'}
    return [dict(zip(parts['layouts'][row[0]], row[1:], strict=True)) for row in parts['rows']]


class OriginalPartRecordPresentationTests(unittest.TestCase):
    def test_heterogeneous_fields_null_absence_and_all_original_lines_survive(self):
        parts = []
        for index in range(100):
            part = dict(locator='第' + str(index) + '页', reading_start=0, reading_end=300,
                        original_characters=300, indexed_lines=[[index, '原文\n单位GWh']])
            if index % 2 == 0:
                part['pdf_table'] = None
            else:
                part['pdf_table'] = dict(page=index, rows=2, columns=3)
            if index % 3 == 0:
                part.pop('pdf_table')
            parts.append(part)
        prompt = dict(untrusted_sources=[dict(id='one', parts=copy.deepcopy(parts))])
        _compact_original_part_records(prompt)
        encoded = prompt['untrusted_sources'][0]['parts']
        self.assertIsInstance(encoded, dict)
        self.assertEqual(canonical(restore_parts(encoded)), canonical(parts))
        self.assertLess(len(canonical(encoded)), len(canonical(parts)))
        self.assertIn('parts', prompt['original_prompt_encoding'])

    def test_no_extra_format_for_empty_or_unprofitable_short_sources(self):
        for parts in ([], [dict(locator='段落1', indexed_lines=[[0, '原文']])]):
            prompt = dict(untrusted_sources=[dict(parts=copy.deepcopy(parts))])
            _compact_original_part_records(prompt)
            self.assertEqual(prompt, dict(untrusted_sources=[dict(parts=parts)]))

    def test_real_failed_capacity_keeps_all_read_text_and_exact_output_schema(self):
        original = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        task = json.loads(SOURCE.read_text())
        supplied = payload(task)
        before = copy.deepcopy(supplied)
        with patch('researchhelper.output_schema._compact_original_part_records'):
            uncompressed = original_span_prompt(supplied, task)
        shown = original_span_prompt(supplied, task)
        schema = original_span_output_schema(task, supplied['untrusted_sources'])
        wire = provider_request_schema(task, schema, ORIGINAL_SPAN_PROTOCOL)
        self.assertLessEqual(len(canonical(shown)) + len(canonical(wire)), MAX_CONTEXT_CHARACTERS)
        self.assertEqual(schema, original_span_output_schema(task, before['untrusted_sources']))
        self.assertEqual(supplied, before)
        for source, prior in zip(shown['untrusted_sources'], uncompressed['untrusted_sources'], strict=True):
            parts = restore_parts(source['parts'])
            self.assertEqual(canonical(parts), canonical(prior['parts']))
            self.assertTrue(parts)
            self.assertTrue(all('locator' in part and 'indexed_lines' in part for part in parts))
            self.assertEqual(len(parts), len(before['untrusted_sources'][0]['parts']))
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), original)


if __name__ == '__main__':
    unittest.main()
