"""Authored offline ETF delivery control, never autonomous model acceptance.

Uses the retained official NAV and the existing synthetic close fixture. Both
inputs must pass their real codecs and validators before calculation/report.
"""
import copy
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest

try:
    from . import test_sse_etf_price_context as fixtures
except ImportError:
    import test_sse_etf_price_context as fixtures

from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import METHOD_REGISTRY
from researchhelper.output_schema import (original_span_output_schema,structured_json_completion,
    ORIGINAL_SPAN_PROTOCOL)
from researchhelper.reasoning import extractable_source_views
from researchhelper.source_reader import read,binding,validate_read_specs
from researchhelper.evidence import extract_material,material_record
from researchhelper import argument_support

ROOT=Path(__file__).resolve().parents[1]
METHOD='etf_daily_nav_premium_v1'


class SSENavDeliveryChainTests(unittest.TestCase):
    def setUp(self):
        self.control=fixtures.SSEETFPcfPriceContextTests()
        self.control.setUp();self.addCleanup(self.control.doCleanups)
        folder=tempfile.TemporaryDirectory(prefix='rh-nav-chain-');self.addCleanup(folder.cleanup)
        self.resources=Path(os.environ.get('RESEARCHHELPER_SSE_ETF_RESOURCES',str(ROOT)))
        self.archive=ResearchArchive(folder.name)
        self.agent=ResearchAgent(self.archive,self.resources,offline=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task=copy.deepcopy(self.control.task);self.task['revision']=0
        self.agent._adopt_method_knowledge(self.task)
        self.archive.save(self.task)

    def selections(self):
        task=self.task;views=extractable_source_views(task)
        schema=original_span_output_schema(task,views)
        wire=fixtures.literal(schema);wire['selections']=[]
        for kind in ('unit_nav','close'):
            source=self.control.nav if kind=='unit_nav' else self.control.price
            ref='source_'+str(next(i for i,v in enumerate(views,1) if v['id']==source['id']))
            options=[v for v in schema['properties']['selections']['items']['anyOf']
                if v['properties']['source_ref']['enum']==[ref]]
            variant=next((v for v in options if v['properties'].get('additional_contexts',{})
                .get('items',{}).get('anyOf')),options[0])
            item=fixtures.literal(variant)
            shape=variant['properties']['selection']['properties']['evidence_specs']['items']['anyOf'][0]
            chosen=fixtures.literal(shape)
            chosen['api_cell_selections']=[shape['properties']['api_cell_selections']['items']['enum'][0]]
            if 'question_ids' in chosen:chosen['question_ids']=['q1']
            review=chosen['review']
            if 'question_ids' in review:review['question_ids']=['q1']
            if 'allowed_natures' in review:review['allowed_natures']=['已实现事实']
            if 'conditions' in review:review['conditions']=['仅本次同日原值比较，不证明历史可得、实时套利或投资收益。']
            if 'reason' in review:review['reason']='离线显式选择控制，依次核验原值、时点与金融口径。'
            item['selection']['evidence_specs']=[chosen]
            if kind=='close':
                item['additional_contexts']=[fixtures.literal(
                    variant['properties']['additional_contexts']['items']['anyOf'][0])]
            wire['selections'].append(item)
        return json.loads(structured_json_completion(json.dumps(wire),schema,
            protocol=ORIGINAL_SPAN_PROTOCOL,context=(task,views)))['evidence_specs']

    def proposal(self):
        specs=self.selections()
        nav,price=(next(x for x in specs if x['artifact_id']==source['id'])
            for source in (self.control.nav,self.control.price))
        return dict(evidence=[],evidence_specs=specs,data_requests=[],
            calculations=[dict(id='daily_nav_check',method=METHOD,
                inputs=dict(nav=nav['id'],price=price['id']),question_ids=['q1'],assumptions={},
                parameters=dict(METHOD_REGISTRY[METHOD]['parameters'],valuation_date='2026-10-21'))],
            answers=dict(q1=dict(numeric_segments=dict(answer=[
                dict(text='离线受控样例的同日单位净值为'),
                dict(source='evidence',ref=nav['id'],path='value',format='signed',digits=4),
                dict(text='元，示例收盘价为'),
                dict(source='evidence',ref=price['id'],path='value',format='signed',digits=4),
                dict(text='元。二者比较不证明可实现套利或投资收益。')]),
                refs=[nav['id'],price['id']],calculation_refs=['daily_nav_check'])),
            theses=[],judgment_reviews=[])

    def reading_bundle(self,artifact_ids=None):
        payload='离线读取控制原件，不提供金融判断。'.encode()
        material=material_record(self.task,extract_material('reading-control.txt',payload),payload)
        self.task['materials'].append(material)
        self.archive.save(self.task)
        request=dict(artifact_id=material['id'],snapshot_hash=material.get('snapshot_hash',material['hash']),queries=['离线读取'],parts=[],
            reason='核对实际读取来源，不批准金融事实。',question_ids=['q1'])
        return read(self.task,[request],binding(self.task),artifact_ids=artifact_ids)

    def test_selected_api_cells_reach_checkpoint_calculation_save_and_export(self):
        proposal=self.proposal()
        bundle=self.reading_bundle()
        validate_read_specs(self.task,proposal['evidence_specs'],bundle)
        result=self.agent.start(self.task['id'],proposal,defer_delivery=True)
        self.assertTrue(result.get('host_checkpoint'),result['gaps'])
        self.assertEqual(len(result['evidence']),2,result['gaps'])
        self.assertEqual(len(result['calculations']),1,result['gaps'])
        calculation=result['calculations'][0]
        expected=(Decimal('4.18')/Decimal('4.1856')-1)*100
        self.assertLess(abs(Decimal(str(calculation['output']['value']))-expected),Decimal('0.0000000001'))
        request=self.agent.argument_review_inputs(self.task['id'])['request']
        refs=['evidence:'+x['id'] for x in result['evidence']]+['calculation:'+calculation['id']]
        review=dict(version=argument_support.VERSION,review_hash=request['review_hash'],
            questions=[dict(question_id='q1',status='answered',missing_evidence=[],reason='仅验证离线受控链路。')],
            reviews=[dict(target_id=t['id'],segments=[dict(text=t['text'],verdict='supported',
                basis_refs=refs,explanation='仅验证同日原值与计算，不批准套利或收益判断。')]) for t in request['targets']])
        self.agent.submit_argument_review(self.task['id'],review)
        done=self.agent.start(self.task['id'])
        self.assertEqual(done['status'],'已完成',done['gaps'])
        report=self.agent.export(done['id'])
        self.assertEqual(len(report['content']['calculations']),1)
        self.assertNotIn('<details',report['html'])
        from lxml import html as dom
        tree=dom.document_fromstring(report['html'])
        document=json.loads(tree.get_element_by_id('rh-publication-document').text)
        figures=[row['spec'] for row in document['blocks'] if row['kind']=='chart']
        self.assertEqual(len(figures),1)
        self.assertEqual(figures[0]['unit_type'],'fund_unit_price')
        self.assertTrue(tree.xpath('//main//figure//*[contains(concat(" ",normalize-space(@class)," ")," chart ")]'))
        self.assertNotIn('元元',tree.find('.//main').text_content())
        self.assertEqual(done['ledger']['used']['model_calls'],0)
        reopened=ResearchAgent(self.archive,self.resources,offline=True,require_argument_support=True)
        try:self.assertEqual(reopened.export(done['id']),report)
        finally:reopened.close_runtime()
        destination=os.environ.get('RESEARCHHELPER_NAV_CHAIN_EXPORT')
        if destination:
            Path(destination).write_text(report['html'],encoding='utf-8')
            Path(destination).with_suffix('.json').write_text(json.dumps(report['content'],ensure_ascii=False),encoding='utf-8')


if __name__=='__main__':
    unittest.main()
