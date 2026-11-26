"""Actual phase wiring exposes current filters and complete real choices."""
import copy
import json
import unittest
from unittest.mock import patch

from researchhelper.evidence import data_request_contract,validate_ifind_report_request
from researchhelper.reasoning import model_object,PHASE_FIELDS,FILING_SELECTION_GUIDANCE_VERSION

from tests.test_filing_choice_coverage import CATALOGUE_COUNT, retained_filing_task


class Recorder:
    def __init__(self):self.calls=[]
    def reason_phase(self,task,messages,phase):
        self.calls.append(dict(phase=phase,messages=copy.deepcopy(messages)))
        return json.dumps({field:[] if field in ('questions','data_requests','calculations','artifact_ids','question_ids')
            else '仅检查阶段契约，无实际动作' if field=='stop_reason' else '' for field in PHASE_FIELDS[phase]})


class FilingSelectionPromptContractTests(unittest.TestCase):
    def test_plan_and_supplement_receive_same_unfiltered_option_without_forcing_901(self):
        task = retained_filing_task()
        before = copy.deepcopy(task['ledger'])
        recorder=Recorder()
        contract=data_request_contract(task)
        with patch('socket.socket.connect',side_effect=AssertionError('No real calls')):
            for phase in ('plan','supplement'):
                result,receipt=model_object(recorder,task,phase,dict(question=task['question'],
                    questions=task['questions'],data_request_contract=contract),PHASE_FIELDS[phase])
                self.assertEqual(receipt['filing_selection_guidance_version'],FILING_SELECTION_GUIDANCE_VERSION)
        for call in recorder.calls:
            user=json.loads(call['messages'][1]['content'])
            tools=user['data_request_contract']['tools']
            directory=next(item for item in tools if item['operation']=='report_query')
            self.assertEqual(directory['parameter_options']['functionpara'],[{}, {'reportType':'901'}])
            self.assertNotIn('functionpara',directory['fixed_parameters'])
            original=next(item for item in tools if item['operation']=='filing_original')
            self.assertEqual(len(original['available_selections']), CATALOGUE_COUNT)
            self.assertTrue(any('2026年中期报告' in item['title'] for item in original['selection_details']))
            self.assertNotIn('类型901、',call['messages'][0]['content'])
            for selected in ({},{'reportType':'901'}):
                params=dict(directory['example']['parameters'],functionpara=selected)
                validate_ifind_report_request(task,params)
        self.assertEqual(task['ledger'],before)


if __name__=='__main__':unittest.main()
