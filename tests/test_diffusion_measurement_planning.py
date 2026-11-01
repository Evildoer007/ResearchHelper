"""Literal diffusion-index units and research obligations, not guessed facts.

The closed failed macro task is read-only. Only an isolated plan copy changes
unit dimension to test the existing percent wire; no old study is reopened,
no actual numeric value is adopted and no source approval is created.
"""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.data_requirements import GUIDANCE, normalized, unit_matches
from researchhelper.output_schema import original_span_output_schema
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import extractable_source_views

FIXTURE=ROOT/'result/acceptance/one-sentence-research-9qxpji9j/task.json'


class DiffusionMeasurementPlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw=FIXTURE.read_bytes();cls.before=hashlib.sha256(raw).hexdigest()
        cls.task=json.loads(raw);cls.views=extractable_source_views(cls.task)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),self.before)

    @staticmethod
    def allowed_units(schema):
        found=set()
        def visit(node):
            if isinstance(node,dict):
                if 'unit' in node.get('properties',{}):
                    found.update(node['properties']['unit'].get('enum',[]))
                for child in node.values():visit(child)
            elif isinstance(node,list):
                for child in node:visit(child)
        visit(schema['properties']['requirements']['properties']['requirement_1'])
        return found

    def test_actual_failed_plan_forced_points_instead_of_literal_percent(self):
        schema=original_span_output_schema(self.task,self.views)
        self.assertEqual(self.allowed_units(schema),{'点'})
        text='\n'.join(part['text'] for view in self.views for part in view['parts'])
        self.assertIn('制造业采购经理指数',text)
        self.assertIn('%',text)

    def test_percent_obligation_offers_existing_literal_percent_protocol(self):
        task=copy.deepcopy(self.task)
        for question in task['questions']:
            for spec in question['evidence_need_constraints'].values():
                if spec['unit_dimension']=='index':spec['unit_dimension']='percentage'
            normalized(question)
        task['input_hash']=input_fingerprint(task)
        with patch('socket.socket.connect',side_effect=AssertionError('Offline only')):
            schema=original_span_output_schema(task,self.views)
        self.assertIn('%',self.allowed_units(schema))
        self.assertNotIn('点',self.allowed_units(schema))
        self.assertEqual(task['evidence'],[])

    def test_market_index_and_amount_gates_are_not_relaxed(self):
        self.assertFalse(unit_matches('%','index'))
        self.assertFalse(unit_matches('%','amount'))
        self.assertTrue(unit_matches('点','index'))
        self.assertTrue(unit_matches('%','percentage'))

    def test_plan_distinguishes_measurement_names_and_reasoning_obligations(self):
        self.assertIn('不按指标名称中的指数二字决定',GUIDANCE)
        self.assertIn('不能把%改写成点或解释为增长率',GUIDANCE)
        self.assertIn('不要求外部原件必须逐字陈述研究结论',GUIDANCE)
        self.assertIn('仍须分析竞争解释和风险',GUIDANCE)


if __name__=='__main__':unittest.main()
