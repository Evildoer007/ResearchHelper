"""Route by the registered output capability before consuming data budget."""
import copy
from pathlib import Path
import tempfile
import unittest
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ContractError,fingerprint
from researchhelper.financial_sources import (financial_query_shapes,validate_financial_acquisition_role)
from researchhelper.planning import input_fingerprint,question_specs
from researchhelper.reasoning import validate_plan
from researchhelper.evidence import data_request_contract
from researchhelper.output_schema import acquisition_output_schema
from researchhelper.reports import compose

POLICY='financial_acquisition_route_version'


class FinancialAcquisitionRouteTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.agent=ResearchAgent(ResearchArchive(Path(temporary.name)),ROOT,offline=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task=self.agent.create('核对证券行业公司半年报营业收入及归母净利润',entry='混合',
            objects=['000166.SZ','000686.SZ'],research_mode='current',questions=[dict(id='q1',kind='fact',
                question='核对证券行业公司半年报营业收入及归母净利润',evidence_needs=['营业收入','归母净利润'])])
        self.need=dict(id='financial_1',source='ifind',operation='smart_stock_picking',
            parameters=dict(searchstring='000166.SZ2026年半年报营业收入、归属于母公司所有者的净利润',searchtype='stock'),
            question_ids=['q1'],selection_reason='独立离线请求路径检查')

    def proposal(self,needs):
        return dict(questions=question_specs(self.task),data_requests=needs,calculations=[])

    def test_new_policy_is_bound_and_frozen_but_old_tasks_remain_unchanged(self):
        self.agent._adopt_method_knowledge(self.task)
        self.task['gaps']=['独立离线夹具尚未研究或获取原件，不批准金融事实。']
        self.assertEqual(self.task[POLICY],1)
        self.assertEqual(compose(self.task)[POLICY],1)
        old=copy.deepcopy(self.task);old.pop(POLICY)
        self.assertNotEqual(input_fingerprint(old),input_fingerprint(self.task))
        old['input_hash']=input_fingerprint(old)
        validate_financial_acquisition_role(old,self.need)
        self.assertNotIn(POLICY,compose(old))

    def test_plan_and_shared_dispatch_reject_unusable_amount_fanout_before_io(self):
        needs=[dict(self.need,id='financial_'+str(i)) for i in range(1,49)]
        before=fingerprint(self.task)
        with self.assertRaisesRegex(ContractError,'不能完成本问题的金额取证'):
            validate_plan(self.task,self.proposal(needs))
        with self.assertRaisesRegex(ContractError,'不能完成本问题的金额取证'):
            self.agent._validated_data_needs(self.task,needs)
        self.assertEqual(fingerprint(self.task),before)
        self.assertEqual(self.task['ledger']['used']['data_requests'],0)

    def test_metadata_only_question_can_inspect_the_raw_return_without_approval(self):
        question=self.task['questions'][0]
        question.update(evidence_needs=['返回列名'],evidence_need_scopes={'返回列名':'query_provenance'})
        validate_financial_acquisition_role(self.task,self.need)
        self.agent._validated_data_needs(self.task,[self.need])
        shapes=financial_query_shapes(self.task)
        self.assertEqual(shapes[0]['raw_query_question_ids'],['q1'])
        self.assertEqual(set(shapes[0]['available_codes']),set(self.task['objects']))
        self.assertFalse(shapes[0]['numeric_evidence_registered'])
        question['method_needs']=['net_profit_margin_v1']
        with self.assertRaises(ContractError):validate_financial_acquisition_role(self.task,self.need)

    def test_industry_discovery_and_original_directory_are_not_blocked(self):
        discovered=dict(self.need,parameters=dict(searchstring='证券行业股票',searchtype='stock'))
        self.agent._validated_data_needs(self.task,[discovered])
        directory=dict(self.need,operation='report_query',parameters=dict(codes='000166.SZ',functionpara={},
            beginrDate='2026-07-01',endrDate='2026-10-20',
            outputpara='reportDate:Y,thscode:Y,secName:Y,reportTitle:Y,pdfURL:Y,seq:Y'))
        # The role guard does not invent a second catalogue validator.
        validate_financial_acquisition_role(self.task,directory)
        shape=financial_query_shapes(self.task)[0]
        self.assertTrue(shape['object_discovery'])
        self.assertEqual(shape['available_codes'],[])

    def test_navigation_schema_exposes_only_discovery_not_financial_code_pattern(self):
        contract=data_request_contract(self.task)
        schema=acquisition_output_schema(self.task,dict(data_request_contract=contract))
        branches=schema['properties']['data_requests']['items']['anyOf']
        smart=[b for b in branches if b['properties']['operation']['enum']==['smart_stock_picking']]
        self.assertEqual(len(smart),1)
        patterns=smart[0]['properties']['parameters']['properties']['searchstring']['anyOf']
        self.assertEqual(len(patterns),1)
        self.assertIn('产业链',patterns[0]['pattern'])
        self.assertNotIn('半年报',patterns[0]['pattern'])

    def test_metadata_permission_does_not_leak_to_a_sibling_numeric_question(self):
        self.task['questions'].append(dict(self.task['questions'][0],id='q2',
            evidence_needs=['返回列名'],evidence_need_scopes={'返回列名':'query_provenance'}))
        shape=financial_query_shapes(self.task)[0]
        self.assertEqual(shape['raw_query_question_ids'],['q2'])
        with self.assertRaises(ContractError):validate_financial_acquisition_role(self.task,self.need)
        validate_financial_acquisition_role(self.task,dict(self.need,question_ids=['q2']))

    def test_unknown_or_boolean_policy_cannot_silently_use_the_legacy_route(self):
        for version in (True,2,'1'):
            self.task[POLICY]=version
            with self.assertRaises(ContractError):validate_financial_acquisition_role(self.task,self.need)
