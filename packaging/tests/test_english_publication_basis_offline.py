"""Literal bilingual release cues, not automatic historical availability.

All materials are fabricated and explicitly marked as synthetic. No model or
supplier is called; the preserved Chinese fixture only supplies test setup.
"""

import copy
import unittest

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import validate_publication_basis
from researchhelper.reasoning import evidence_contract, source_views, validate_model_specs
from tests import test_publication_quote_binding_offline as fixtures
from tests import test_autonomous_reasoning_offline as answers


class EnglishPublicationBasisTests(unittest.TestCase):
    setUp = fixtures.PublicationQuoteBindingTests.setUp
    prepare = fixtures.PublicationQuoteBindingTests.prepare

    def test_explicit_english_release_is_usable_in_real_host_workflow(self):
        task, spec = self.prepare('Published on 2026-07-19. This is a synthetic policy.')
        validate_model_specs(task, [spec])
        done = self.agent.start(task['id'], {'evidence_specs': [spec], **answers.AutonomousReasoningTests.answer()})
        self.assertEqual(done['status'], '已完成')
        self.assertEqual(len(done['evidence']), 1)
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertEqual(done['ledger']['used']['data_requests'], 0)

    def test_explicit_labels_and_case_are_readable_without_changing_dates(self):
        for release in ('Publication date: 2026-07-19.', 'RELEASE DATE: 2026-07-19.',
                        'Date of publication: 2026-07-19.', 'Disclosed on 2026-07-19.',
                        '2026-07-19: Released to the public.'):
            with self.subTest(release=release):
                task, spec = self.prepare(release)
                validate_publication_basis(spec, task['materials'])

    def test_candidates_preserve_original_bytes_and_do_not_propose_timestamps(self):
        task, _ = self.prepare('Published on 2026-07-19. Synthetic material.')
        before = fingerprint(task)
        contract = evidence_contract(task)
        candidate = contract['sources'][0]['publication_candidates'][0]
        self.assertIn('Published on 2026-07-19', candidate['basis_ref']['quote'])
        self.assertNotIn('published_at', candidate)
        self.assertFalse(contract['publication_candidate_contract']['candidate_is_approval'])
        self.assertFalse(contract['publication_candidate_contract']['automatic_timestamp_inference'])
        self.assertEqual(fingerprint(task), before)

    def test_planned_release_cannot_become_confirmed_by_clipping_prefix(self):
        for release, quote in (
            ('Scheduled release date: 2026-07-19.', 'release date: 2026-07-19'),
            ('Expected to be published on 2026-07-19.', 'published on 2026-07-19'),
            ('Planned publication date: 2026-07-19.', 'publication date: 2026-07-19'),
            ('Release date scheduled for 2026-07-19.', 'Release date scheduled for 2026-07-19'),
            ('Published by 2026-07-19.', 'Published by 2026-07-19'),
        ):
            with self.subTest(release=release):
                task, spec = self.prepare(release, quote=quote)
                with self.assertRaises(ContractError):
                    validate_publication_basis(spec, task['materials'])

    def test_negative_release_cannot_become_confirmed_by_clipping_prefix(self):
        for release, quote in (
            ('Not published on 2026-07-19.', 'published on 2026-07-19'),
            ('Never publicly disclosed on 2026-07-19.', 'disclosed on 2026-07-19'),
            ('Not yet released on 2026-07-19.', 'released on 2026-07-19'),
        ):
            with self.subTest(release=release):
                task, spec = self.prepare(release, quote=quote)
                with self.assertRaises(ContractError):
                    validate_publication_basis(spec, task['materials'])

    def test_release_marker_must_not_match_unreleased_or_republished_word(self):
        for release in ('Unreleased on 2026-07-19.', 'Republished on 2026-07-19.'):
            with self.subTest(release=release):
                task, spec = self.prepare(release)
                self.assertEqual(evidence_contract(task)['sources'][0]['publication_candidates'], [])
                with self.assertRaises(ContractError):
                    validate_publication_basis(spec, task['materials'])

    def test_effective_date_and_reporting_date_cannot_replace_release_date(self):
        for release in (
            'Published on 2026-07-25; effective date: 2026-07-19.',
            'Released report period ending 2026-07-19; publication date: 2026-07-25.',
            'Published report as of 2026-07-19; release date: 2026-07-25.',
        ):
            with self.subTest(release=release):
                task, spec = self.prepare(release)
                with self.assertRaises(ContractError):
                    validate_publication_basis(spec, task['materials'])

    def test_correct_release_and_separate_effective_date_remain_usable(self):
        task, spec = self.prepare('Publication date: 2026-07-19; effective date: 2026-07-21.',
                                  quote='Publication date: 2026-07-19')
        validate_publication_basis(spec, task['materials'])

    def test_release_after_cutoff_remains_rejected_in_actual_host_workflow(self):
        task, spec = self.prepare('Published on 2026-07-25.', published='2026-07-25')
        done = self.agent.start(task['id'], {'evidence_specs': [spec], **answers.AutonomousReasoningTests.answer()})
        self.assertEqual(done['status'], '部分完成')
        self.assertEqual(done['evidence'], [])
        self.assertTrue(done['rejected_evidence'])

    def test_same_original_and_snapshot_binding_cannot_be_borrowed(self):
        task, spec = self.prepare('Published on 2026-07-19.')
        for change in ({'artifact_id': 'another-original'}, {'snapshot_hash': 'modified-content'}):
            with self.subTest(change=change):
                forged = copy.deepcopy(spec)
                forged['publication']['basis_ref'].update(change)
                with self.assertRaises(ContractError):
                    validate_publication_basis(forged, task['materials'])

    def test_candidates_only_search_the_reading_span(self):
        task, _ = self.prepare('Intro. ' * 100 + 'Published on 2026-07-19.' + ' More detail.' * 100)
        views = source_views(task)
        part = views[0]['parts'][1]
        original = part['text']
        views[0]['parts'] = [{'locator': part['locator'], 'text': original[:30],
            'reading_start': 0, 'reading_end': 30, 'original_characters': len(original)}]
        self.assertEqual(evidence_contract(task, views)['sources'][0]['publication_candidates'], [])
        start = original.index('Published on')
        end = start + len('Published on 2026-07-19.')
        views[0]['parts'] = [{'locator': part['locator'], 'text': original[start:end],
            'reading_start': start, 'reading_end': end, 'original_characters': len(original)}]
        candidate = evidence_contract(task, views)['sources'][0]['publication_candidates'][0]
        self.assertEqual(candidate['reading_range'], {'start': start, 'end': end})

    def test_date_only_anchor_does_not_supply_clock_time_or_timezone(self):
        task, spec = self.prepare('Published on 2026-07-19.', published='2026-07-19T09:00:00+08:00')
        with self.assertRaises(ContractError):
            validate_publication_basis(spec, task['materials'])
