"""The acquired-data inventory must resolve the actual plan's own needs."""
import copy
import importlib
import json
import socket
import unittest
from unittest.mock import patch

SOURCE = importlib.import_module((__package__+'.' if __package__ else '')+'test_financial_data_module').SOURCE
planned_data_coverage = importlib.import_module((__package__+'.' if __package__ else '')+
    'financial_data_coverage_fixture').planned_data_coverage
from researchhelper.financial_data import half_year_financial_data


class SingleSecurityDataRequirementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = SOURCE.read_bytes()
        cls.task = json.loads(cls.raw)
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('No external I/O')):
            cls.data = half_year_financial_data(cls.task, '002594.SZ', ['20260630', '20250630'])

    @classmethod
    def tearDownClass(cls):
        assert SOURCE.read_bytes() == cls.raw

    def test_nine_actual_requirements_have_nonempty_resolvable_addresses(self):
        coverage = planned_data_coverage(self.task, self.data)
        self.assertEqual(len(coverage), 9)
        self.assertEqual({row['question_id'] for row in coverage}, {'q2', 'q4'})
        self.assertTrue(all(row['original_data_available'] and not row['evidence_adopted']
                            and not row['question_answered'] for row in coverage))

    def test_other_task_or_input_or_version_cannot_claim_the_same_data_product(self):
        for field, value in (('id','another_task'), ('version',2), ('batch',2), ('input_hash','changed')):
            task = dict(self.task, **{field:value})
            with self.subTest(field=field), self.assertRaises(AssertionError):
                planned_data_coverage(task, self.data)

    def test_unknown_new_requirement_does_not_become_covered_by_prior_data(self):
        task = copy.deepcopy(self.task)
        question = next(question for question in task['questions'] if question['id'] == 'q4')
        question['evidence_coverage']['items'].append(dict(id='new_need', text='其他同行的估值和订单'))
        with self.assertRaises(AssertionError): planned_data_coverage(task, self.data)


if __name__ == '__main__':
    unittest.main()
