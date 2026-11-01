"""Retained-page metadata and lossless input checks; no paid calls or retries."""
import base64
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, canonical
from researchhelper.evidence import extract_material
from researchhelper.html_material import extract_html
from researchhelper.material_parser import verified_material
from researchhelper.model_presentation import GUIDE, present, restore
from researchhelper.reasoning import evidence_contract, extractable_source_views, model_object

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT/'result/acceptance/one-sentence-research-8x1tzgha/task.json'


class HTMLMetadataAndSharingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ORIGINAL if ORIGINAL.exists() else ORIGINAL.with_suffix('.json.gz')
        cls.raw = cls.path.read_bytes()
        cls.task = json.loads(gzip.decompress(cls.raw) if cls.path.suffix == '.gz' else cls.raw)
        cls.originals = [s['snapshot'] for s in cls.task['source_records'] if s['operation'] == 'release_original']

    def setUp(self):
        guard = patch('socket.socket.connect', side_effect=AssertionError('Offline retained original only'))
        guard.start(); self.addCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            loader = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            loader.start(); self.addCleanup(loader.stop)

    def tearDown(self):
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_real_metadata_is_exact_tag_and_does_not_replace_or_delete_body(self):
        for original in self.originals:
            payload = base64.b64decode(original['original_base64'], validate=True)
            legacy = extract_material('original.html', payload, extract_version='html-1')
            self.assertEqual(legacy['parts'], original['parts'])
            current = extract_material('original.html', payload)
            self.assertEqual(current['extract_version'], 'html-2')
            self.assertEqual(current['hash'], hashlib.sha256(payload).hexdigest())
            metadata = [p for p in current['parts'] if 'metadata_field' in p]
            body = [p for p in current['parts'] if 'metadata_field' not in p]
            self.assertEqual(body, legacy['parts'])
            dates = [p for p in metadata if p['metadata_field'] == 'PubDate']
            self.assertEqual(len(dates), 1)
            self.assertIn('2026/09/30 09:30', dates[0]['text'])
            for part in metadata:
                self.assertIn(part['text'], payload.decode('utf-8'))
                self.assertNotIn('published_at', part)
            self.assertEqual(current['source_check'], '待核验')
            self.assertEqual(current['applicability_check'], '待核验')
            self.assertNotIn('published_at', current)

    def test_both_extraction_versions_reverify_and_metadata_tampering_is_not_accepted(self):
        payload = base64.b64decode(self.originals[0]['original_base64'], validate=True)
        for version in ('html-1', 'html-2'):
            material = extract_material('original.html', payload, extract_version=version)
            checked = verified_material(material, payload)
            self.assertEqual(checked['parts'], material['parts'])
            damaged = copy.deepcopy(material); damaged['parts'][0]['text'] += '并非原文'
            with self.assertRaises(ContractError): verified_material(damaged, payload)
        with self.assertRaises(ContractError): extract_html(payload, extract_version='html-foreign')

    def test_meta_only_page_does_not_become_readable_body(self):
        payload = b'<html><head><meta name="PubDate" content="2026/09/30 09:30"></head><body><script>fake body</script></body></html>'
        parsed = extract_material('metadata.htm', payload)
        self.assertEqual(parsed['status'], '解析受限')
        self.assertEqual(len(parsed['parts']), 1)
        self.assertEqual(parsed['parts'][0]['metadata_field'], 'PubDate')
        self.assertNotIn('fake body', str(parsed['parts']))

    def test_metadata_can_be_located_by_the_normal_reader_without_network_or_approval(self):
        from researchhelper.archive import ResearchArchive
        from researchhelper.engine import ResearchAgent
        folder = tempfile.TemporaryDirectory(prefix='html-metadata-read-', dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True, isolate_materials=False)
        self.addCleanup(agent.close_runtime)
        task = agent.create('核对网页标题和PubDate原字段，不确认历史首发时间', intent='原件内容核对')
        payload = base64.b64decode(self.originals[0]['original_base64'], validate=True)
        task = agent.add_material(task['id'], 'nbs-release.html', payload)
        catalog = agent.source_catalog(task['id'])
        item = catalog['items'][0]
        selected = dict(item['request_binding'], queries=['PubDate'], parts=[],
                        reason='仅定位实际元数据字段，不确认首发时刻', question_ids=['q1'])
        bundle = agent.read_materials(task['id'], [selected], catalog['binding'])
        self.assertTrue(any('PubDate' in part['text'] for view in bundle['views'] for part in view['parts']))
        current = agent.archive.get(task['id'])
        self.assertEqual(current['evidence'], [])
        self.assertEqual(current['ledger']['used']['data_requests'], 0)
        self.assertEqual(current['ledger']['used']['model_calls'], 0)
        self.assertIsNone(agent.model)

    def test_published_modified_and_ambiguous_minute_values_remain_separate_raw_fields(self):
        payload = b'<html><head><meta name="PubDate" content="2026/09/30 09:30"><meta property="article:modified_time" content="2026-10-01T10:30:00Z"></head><body><p>body</p></body></html>'
        parsed = extract_material('dates.html', payload)
        fields = [p['metadata_field'] for p in parsed['parts'] if 'metadata_field' in p]
        self.assertEqual(fields, ['PubDate', 'article:modified_time'])
        self.assertNotIn('Asia/Shanghai', canonical(parsed))
        self.assertNotIn('2026/09/30 09:30:00', canonical(parsed))
        self.assertNotIn('published_at', parsed)

    def test_meta_date_is_not_itself_confirmed_first_publication(self):
        from researchhelper.evidence import _validate_publication_quote
        for text, day in (('<meta name="PubDate" content="2026/09/30 09:30">', '2026-09-30'),
                          ('<meta property="article:modified_time" content="2026-10-01T10:30:00Z">', '2026-10-01')):
            with self.subTest(text=text), self.assertRaises(ContractError):
                _validate_publication_quote(text, text, day)

    def test_scripts_templates_resources_unknown_metadata_and_limits_are_not_bypassed(self):
        payload = b'<html><head><script><meta name="PubDate" content="forged"></script><template><meta name="PubDate" content="template"></template><meta name="token" content="private"><meta name="ArticleTitle" content="title"><link href="https://example.invalid/"></head><body><p>body</p></body></html>'
        parts = extract_html(payload)
        self.assertEqual([p['metadata_field'] for p in parts if 'metadata_field' in p], ['ArticleTitle'])
        self.assertNotIn('forged', str(parts)); self.assertNotIn('template', str(parts)); self.assertNotIn('private', str(parts))
        with patch('researchhelper.html_material.MAX_CHARACTERS', 10), self.assertRaises(ContractError):
            extract_html(b'<head><meta name="PubDate" content="2026/09/30 09:30"></head>')

    def test_identical_long_strings_share_without_merging_positions_or_near_matches(self):
        original = '仅本期原始观测，不证明因果或历史可得性。' * 120
        other = original.replace('不证明', '能够证明', 1)
        value = dict(parts=[dict(locator='甲', text=original), dict(locator='乙', text=original),
                            dict(locator='丙', text=other)], flags=[True, 1, 1.0, None, '1'],
                     literal={'$rh_ref': 'not_a_reference'})
        before = copy.deepcopy(value)
        encoded, changed = present(value)
        self.assertTrue(changed)
        self.assertEqual(canonical(restore(encoded)), canonical(value))
        self.assertEqual(value, before)
        self.assertEqual(restore(encoded)['parts'][2]['text'], other)
        self.assertLess(len(canonical(encoded)) + len(GUIDE), len(canonical(value)))

    def test_actual_duplicate_original_text_roundtrips_every_part_and_table(self):
        payload = dict(untrusted_sources=[dict(id=str(i), parts=original['parts'])
            for i, original in enumerate(self.originals)], limits=['当前原件，不证明历史可得性或事件因果'])
        before = canonical(payload)
        encoded, changed = present(payload)
        self.assertTrue(changed)
        self.assertEqual(canonical(restore(encoded)), before)
        self.assertEqual(canonical(payload), before)
        self.assertLess(len(canonical(encoded)) + len(GUIDE), len(before))
        self.assertEqual(len(restore(encoded)['untrusted_sources'][0]['parts']), len(self.originals[0]['parts']))

    def test_actual_extract_dispatch_uses_reversible_input_and_same_sources_without_a_model(self):
        task = copy.deepcopy(self.task)
        task['original_prompt_projection_version'] = 2
        views = extractable_source_views(task)
        payload = dict(untrusted_sources=views, evidence_contract=evidence_contract(task, views))
        original = canonical(task)
        class Prepared(Exception): pass
        class Observer:
            def reason(self, current, messages):
                self.messages = copy.deepcopy(messages)
                raise Prepared()
        observer = Observer()
        with patch('researchhelper.model_presentation.present', wraps=present) as encoder:
            with self.assertRaises(Prepared): model_object(observer, task, 'extract', payload, {'evidence_specs'})
            self.assertEqual(encoder.call_count, 1)
            logical = encoder.call_args.args[0]
        transmitted = json.loads(observer.messages[1]['content'])
        self.assertEqual(transmitted['format'], 'researchhelper.table_input')
        self.assertEqual(canonical(restore(transmitted)), canonical(logical))
        self.assertIn(GUIDE, observer.messages[0]['content'])
        self.assertEqual(canonical(task), original)
        self.assertEqual(restore(transmitted)['untrusted_sources'], logical['untrusted_sources'])


if __name__ == '__main__': unittest.main()
