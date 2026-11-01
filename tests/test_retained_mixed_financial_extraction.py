"""Replay a retained mixed-source extraction boundary, never reopen its study."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import extractable_source_views as actual_views

class RetainedMixedExtractionTests(unittest.TestCase):
    def test_real_retained_api_and_long_original_both_reach_the_reader(self):
        root=Path(__file__).resolve().parents[1]
        path=Path(os.environ.get('RESEARCHHELPER_RETAINED_TASK',str(root/'result/acceptance/one-sentence-research-7gqrup_d/task.json')))
        original=json.loads(path.read_text());task=copy.deepcopy(original)
        selection=copy.deepcopy(task['reasoning_state']['reading']['bundle']['selection'])
        task.update(mixed_atomic_api_reading_version=1,extraction_read_isolation_version=2)
        task['input_hash']=input_fingerprint(task)
        task['reasoning_state']={}
        # This is a no-dispatch local replay. The original ledger is retained
        # in the source file; only this disposable fixture admits the capture.
        task['ledger']['limits']['model_calls']+=4
        resources=Path(os.environ.get('RESEARCHHELPER_IFIND_METADATA_RESOURCES',str(root)))
        with tempfile.TemporaryDirectory() as folder:
            agent=ResearchAgent(ResearchArchive(folder),resources,offline=True,require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            agent._active=lambda *args:None
            agent._save_model_checkpoint=lambda *args:None
            agent._reasoning_available=lambda *args:True
            captured=[]
            class Captured(Exception):pass
            def model(model,task,phase,payload,fields):
                if phase=='read':return dict(requests=selection),dict(phase='read',local_fixture=True)
                self.assertEqual(phase,'extract')
                captured.append(copy.deepcopy(payload));raise Captured()
            calls=[]
            def initially_stale(task):
                calls.append(True)
                views=actual_views(task)
                return [view for view in views if view['kind']!='api'] if len(calls)<=2 else views
            with patch('socket.socket.connect',side_effect=AssertionError('No network')), \
                    patch('researchhelper.engine.model_object',side_effect=model), \
                    patch('researchhelper.engine.extractable_source_views',side_effect=initially_stale),self.assertRaises(Captured):
                agent._extract_model_sources(task)
            self.assertEqual(len(captured),1)
            views=captured[0]['untrusted_sources']
            self.assertEqual({view['kind'] for view in views},{'api','filing'})
            api=next(view for view in views if view['kind']=='api')
            self.assertEqual(api['operation'],'basic_data_service')
            contract=next(source for source in captured[0]['evidence_contract']['sources'] if source['artifact_id']==api['id'])
            self.assertEqual(len(contract['cell_choices']),4)
            self.assertEqual(contract['calculation_context_contract']['status'],'registered_constraints')
            self.assertEqual(json.loads(path.read_text()),original)
