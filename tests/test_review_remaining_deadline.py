"""Final semantic review uses finite remaining research time, never a new budget."""
import copy
import importlib.util
from pathlib import Path
import unittest

# The isolated Skill host loads test files directly, without the development
# repository's tests package. Load this sibling by its actual local path.
spec=importlib.util.spec_from_file_location('reasoned_delivery_capacity_fixture',
    Path(__file__).with_name('test_reasoned_delivery_capacity.py'))
fixture=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class ReviewRemainingDeadlineTests(unittest.TestCase):
    setUp=fixture.ReasonedDeliveryCapacityTests.setUp
    request=fixture.ReasonedDeliveryCapacityTests.request

    def test_formal_review_has_a_finite_three_hundred_second_stage_cap(self):
        _,record=self.request('review_support')
        self.assertEqual(record['timeout_seconds'],300)

    def test_actual_remaining_time_still_keeps_sixty_seconds_for_delivery(self):
        before=copy.deepcopy(self.task['ledger']['limits'])
        self.task['ledger']['used']['seconds']=302
        _,record=self.request('review_support')
        self.assertEqual(record['timeout_seconds'],238)
        self.assertEqual(self.task['ledger']['limits'],before)

    def test_reserved_review_keeps_five_seconds_to_save(self):
        self.task['ledger']['used'].update(model_calls=7,seconds=535)
        _,record=self.request('review_support',delivery=True,delivery_margin=5)
        self.assertEqual(record['timeout_seconds'],60)
        self.assertEqual(self.task['ledger']['used']['model_calls'],8)

    def test_ordinary_analysis_keeps_its_existing_one_hundred_eighty_second_cap(self):
        _,record=self.request('analyze')
        self.assertEqual(record['timeout_seconds'],180)


if __name__=='__main__':unittest.main()
