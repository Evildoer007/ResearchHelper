"""Batch quote wire/schema/decoder must share the same literal need aliases."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ContractError,fingerprint
from researchhelper.evidence import EvidenceCollector
from researchhelper.official_sources import release_choices
from tests.test_official_release_channel import CATALOG
from researchhelper.reasoning import extractable_source_views,evidence_contract
from researchhelper.output_schema import (original_batch_output_schema,original_batch_prompt,
    decode_original_batch,_original_quote_selector)


class BatchQuoteNeedAliasTests(unittest.TestCase):
    def setUp(self):
        private=tempfile.TemporaryDirectory();self.addCleanup(private.cleanup)
        self.agent=ResearchAgent(ResearchArchive(Path(private.name)),ROOT,offline=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        task=self.agent.create('核对600519.SH报告的利润质量说明',objects=['600519.SH'],research_mode='current',
            questions=[dict(id='q1',question='报告对现金流与利润差异如何说明？',kind='fact',
                evidence_needs=['报告对现金流与利润差异的原文说明'],method_needs=['net_profit_margin_v1'])])
        # Uploaded TXT has no budgeted current-original acquisition receipt.
        # Use explicitly synthetic official transport, not forged availability.
        body=('<html><body><p>2026年6月30日</p><p>现金流与利润的差异需要核对非现金调整'
            '和营运资金，不代表盈利质量自动改善。</p></body></html>').encode()
        def returned(task,url,started,timeout,event,**kwargs):
            event.update(transport_isolated=True,network_request_started=True,network_worker_reaped=True)
            return CATALOG if url.endswith('/zxfbhjd/') else body
        collector=EvidenceCollector(offline=False)
        with patch('researchhelper.source_transport.official_html',side_effect=returned):
            catalog=collector.external(task,'official','release_catalog',dict(channel='nbs_release',page='0'),{})
            task['source_records'].append(catalog)
            choice=release_choices(task)[0]
            original=collector.external(task,'official','release_original',
                {key:choice[key] for key in ('catalog_id','release_id')},{})
            task['source_records'].append(original)
        self.task=task
        self.views=extractable_source_views(self.task)
        self.payload=dict(untrusted_sources=self.views,evidence_contract=evidence_contract(self.task,self.views))
        guard=patch('socket.socket.connect',side_effect=AssertionError('Offline quote alias fixture'))
        guard.start();self.addCleanup(guard.stop)

    def wire(self):
        description=_original_quote_selector(self.task,self.views[0])
        schema=original_batch_output_schema(self.task,self.views)
        return dict(version=1,binding_ref=schema['properties']['binding_ref']['enum'][0],sources=[
            dict(source_ref='source_1',selection=dict(binding_ref=description['binding_ref'],text_quotes=[
                dict(id='synthetic_quote',object='600519.SH',nature='已实现事实',question_ids=['q1'],
                    review=dict(scope='current_original_descriptive_only',need_links=[dict(question_id='q1',need_ids=['n1'])]),
                    observation_anchor=0,quote_reference=next(iter(description['refs'])))]))])

    def test_prompt_and_schema_offer_the_same_question_local_alias(self):
        prompt=original_batch_prompt(self.payload,self.task)
        choice=prompt['source_choices'][0]
        self.assertEqual(choice['selection_kind'],'qualitative')
        self.assertEqual(choice['quote_need_choices']['q1'],[dict(id='n1',text='报告对现金流与利润差异的原文说明')])
        self.assertNotEqual(self.payload['evidence_contract']['question_requirements']['q1'][0]['id'],'n1')
        schema=original_batch_output_schema(self.task,self.views)
        result=decode_original_batch(self.wire(),schema,(self.task,self.views))
        self.assertEqual(result['evidence_specs'][0]['review']['evidence_need_texts'],
            {'q1':['报告对现金流与利润差异的原文说明']})

    def test_wrong_alias_or_canonical_hash_is_not_silently_repaired(self):
        canonical=self.payload['evidence_contract']['question_requirements']['q1'][0]['id']
        for reference in ('n2',canonical):
            wire=self.wire();wire['sources'][0]['selection']['text_quotes'][0]['review']['need_links'][0]['need_ids']=[reference]
            with self.assertRaises(ContractError):decode_original_batch(wire,
                original_batch_output_schema(self.task,self.views),(self.task,self.views))

    def test_decode_does_not_change_task_requirements_or_source(self):
        before=fingerprint(self.task);payload_before=copy.deepcopy(self.payload)
        original_batch_prompt(self.payload,self.task)
        decode_original_batch(self.wire(),original_batch_output_schema(self.task,self.views),(self.task,self.views))
        self.assertEqual(fingerprint(self.task),before);self.assertEqual(self.payload,payload_before)
