"""Explicit codes attached to ETF labels are not name-to-code guesses."""
import copy
import tempfile
import unittest

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (IFIND_REPORT_FIELDS, available_security_codes,
    declared_security_codes, direct_code_reference, validate_ifind_report_request)
from researchhelper.planning import input_fingerprint
from tests.market_observation_fixtures import ETF_CLOSES, etf_close_replay, read_captures


class NaturalSecurityDeclarationTests(unittest.TestCase):
    def parse(self, question, **extra):
        return declared_security_codes(dict(question=question, objects=[],
                                           security_declaration_encoding=2, **extra))

    def test_chinese_product_names_and_attached_codes_keep_literal_values(self):
        self.assertEqual(self.parse('比较沪深300ETF510300.SH和沪深300ETF159919.SZ的费率'),
                         ['159919.SZ','510300.SH'])
        self.assertEqual(self.parse('纳斯达克100ETF159941.SZ；上证50etf510050.SH'),
                         ['159941.SZ','510050.SH'])
        self.assertEqual(self.parse('研究600519.SH2026年半年报与00700.HK的披露'),
                         ['00700.HK','600519.SH'])

    def test_named_product_without_complete_code_never_creates_one(self):
        for text in ('研究沪深300ETF','ETF510300','159919','600519.sh','SH600519'):
            with self.subTest(text=text):self.assertEqual(self.parse(text),[])

    def test_embedded_identifiers_short_codes_and_suffixes_do_not_expand_scope(self):
        for text in ('X510300.SH','abcETF510300.SH','prefix_510300.SH',
                     'ETF510300.SHX','ETF510300.SH_1','ETF510300.SH2026abc',
                     'ETF1510300.SH','ETF51030.SH','600519.SHA'):
            with self.subTest(text=text):self.assertEqual(self.parse(text),[])

    def test_legacy_tokens_are_not_silently_upgraded(self):
        declaration=dict(question='沪深300ETF510300.SH；600519.SH2026年半年报',objects=[])
        self.assertEqual(declared_security_codes(declaration),[])
        self.assertEqual(declared_security_codes(declaration,encoding=2),['510300.SH','600519.SH'])
        for invalid in (True,0,3,'2'):
            with self.subTest(invalid=invalid),self.assertRaises(ContractError):
                declared_security_codes(dict(declaration,security_declaration_encoding=invalid))

    def test_fresh_formal_task_exposes_both_codes_to_unchanged_filing_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=ResearchAgent(ResearchArchive(folder),ROOT,offline=True,require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task=agent.create('比较沪深300ETF510300.SH和沪深300ETF159919.SZ的管理费率')
            self.assertEqual(task['security_declaration_encoding'],2)
            self.assertEqual(available_security_codes(task),['159919.SZ','510300.SH'])
            for code in available_security_codes(task):
                params=dict(codes=code,functionpara={},beginrDate='2026-01-01',
                    endrDate='2026-10-01',outputpara=','.join(field+':Y' for field in IFIND_REPORT_FIELDS))
                validate_ifind_report_request(task,params)
                with self.assertRaises(ContractError):
                    validate_ifind_report_request(task,dict(params,codes='510500.SH'))
            legacy=copy.deepcopy(task);legacy.pop('security_declaration_encoding')
            self.assertNotEqual(input_fingerprint(task),input_fingerprint(legacy))
            self.assertNotIn('security_declaration_encoding',agent.create_case('mechanism'))
            self.assertNotIn('security_declaration_encoding',agent.create('核对原文',intent='原件内容核对'))

    def test_original_input_still_bounds_direct_observation_not_model_added_objects(self):
        original = ETF_CLOSES.read_bytes()
        task, quote = etf_close_replay(read_captures(original, grouped=False))
        reference = direct_code_reference(task, quote)
        self.assertIsNotNone(reference)
        self.assertEqual(reference['version'],2)
        self.assertEqual(reference['declaration_encoding'],2)
        # Caller-selected execution objects do not alter the original input.
        task['question']=task['original_input']['question']='沪深300ETF的收盘价'
        task['objects']=['510300.SH']
        task['input_hash']=input_fingerprint(task)
        self.assertIsNone(direct_code_reference(task,task['source_records'][0]))
        self.assertEqual(ETF_CLOSES.read_bytes(), original)

    def test_closed_etf_failure_remains_closed_and_keeps_its_old_grammar(self):
        # A synthetic old task tests the legacy grammar and closed-state readback.
        with tempfile.TemporaryDirectory() as folder:
            archive = ResearchArchive(folder)
            agent = ResearchAgent(archive, ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('沪深300ETF510300.SH的收盘价', entry='ETF', research_mode='current')
                task.pop('security_declaration_encoding')
                task['input_hash'] = input_fingerprint(task)
                task['status'] = '失败'
                archive.save(task)
                restored = archive.get(task['id'])
                self.assertEqual(restored['status'], '失败')
                self.assertEqual(available_security_codes(restored), [])
                self.assertNotIn('security_declaration_encoding', restored)
                self.assertEqual(archive.get(task['id']), restored)
            finally:
                agent.close_runtime()
