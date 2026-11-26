"""Text counts/dates and per-observation periods, without source approval."""
import copy
from pathlib import Path
import tempfile
import unittest

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.data_requirements import FIELD, normalized, satisfies, count_satisfied, validate_plan
from researchhelper.engine import ResearchAgent
from researchhelper.output_schema import planning_output_schema, _matches
from researchhelper.planning import input_fingerprint


class TypedObservationContractTests(unittest.TestCase):
    def test_dated_text_and_multiple_statements_remain_explicit_obligations(self):
        spec=dict(value_kind='text',unit_dimension='not_applicable',minimum_count=2,period_kind='point')
        q=dict(id='q1',evidence_needs=['两次声明日期'],**{FIELD:{'两次声明日期':spec}})
        self.assertEqual(normalized(q)['两次声明日期'],spec)
        def item(day):
            return dict(object='FOMC',text='测试日期原文'+day,period=day,
                        question_ids=['q1'],record_locator={'part':day},
                        applicability_review={'evidence_need_ids':{'q1':[fingerprint('两次声明日期')]}})
        first=item('2026-07-29');second=item('2026-09-16')
        self.assertTrue(satisfies(first,spec))
        self.assertFalse(satisfies(dict(first,period='2026-07'),spec))
        self.assertFalse(satisfies(dict(first,text=' '),spec))
        self.assertFalse(count_satisfied(q,'两次声明日期',[first,copy.deepcopy(first)]))
        self.assertTrue(count_satisfied(q,'两次声明日期',[first,second]))

    def test_text_does_not_discharge_numeric_or_unit_obligations(self):
        spec=dict(value_kind='number',unit_dimension='percentage',minimum_count=1,period_kind='point')
        self.assertFalse(satisfies(dict(text='权重5%',value=None,unit='%',period='2026-10-19'),spec))
        q=dict(evidence_needs=['日期'],**{FIELD:{'日期':dict(spec,value_kind='text')}})
        with self.assertRaises(ContractError):normalized(q)

    def test_index_and_event_needs_validate_without_changing_a_closed_task(self):
        examples = [
            ('核对指数的五个交易日点位', '各交易日收盘点位', 'number', 'index', 5),
            ('核对两次FOMC声明的日期', '两次声明发布日期', 'text', 'not_applicable', 2),
        ]
        with tempfile.TemporaryDirectory() as folder:
            archive = ResearchArchive(Path(folder))
            agent = ResearchAgent(archive, ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            for prompt, need, value_kind, dimension, count in examples:
                with self.subTest(prompt=prompt):
                    task = agent.create(prompt)
                    task.update(status='失败', synthetic=True)
                    archive.save(task)
                    before = archive.get(task['id'])
                    specification = dict(value_kind=value_kind, unit_dimension=dimension,
                                         minimum_count=count, period_kind='point')
                    proposed = [dict(task['questions'][0], evidence_needs=[need], **{FIELD:{need:specification}})]
                    validate_plan(before, proposed)
                    self.assertEqual(archive.get(task['id']), before)

    def test_schema_excludes_incompatible_text_and_numeric_unit_pairs(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=ResearchAgent(ResearchArchive(Path(folder)),ROOT,offline=True,require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task=agent.create('核对两次声明的日期')
            self.assertEqual(task['typed_observation_contract_version'],2)
            schema=planning_output_schema(task)
            question=schema['properties']['main_question']['anyOf'][0]
            need=question['properties']['evidence_needs']['items']
            sample=dict(text='日期',value_kind='text',unit_dimension='not_applicable',minimum_count=2,period_kind='point')
            self.assertTrue(_matches(sample,need))
            self.assertFalse(_matches(dict(sample,unit_dimension='amount'),need))
            self.assertFalse(_matches(dict(sample,value_kind='number'),need))
            self.assertTrue(_matches(dict(sample,value_kind='number',unit_dimension='price'),need))
            old=input_fingerprint(task);task['typed_observation_contract_version']=1
            self.assertNotEqual(old,input_fingerprint(task))


if __name__=='__main__':unittest.main()
