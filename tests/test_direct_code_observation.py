"""Actual retained quote replay plus negative boundaries; no external calls."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if importlib.util.find_spec('researchhelper') is None:
    from core.bootstrap import configure_imports
    configure_imports()
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import (candidate_evidence, direct_code_reference,
    validate_provenance, validate_direct_code_identity, DIRECT_CODE_LIMITATION)
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.reasoning import evidence_contract, API_EXTRACTION_GUIDANCE_VERSION, analysis_contract, model_object
from researchhelper.planning import input_fingerprint
from researchhelper.output_schema import (api_extraction_output_schema, decode_api_extraction_output,
                                         _planned_request_schemas, _matches)

ORIGINAL = ROOT / 'result/acceptance/installed-natural-research-941_jdsj/task.json'


class DirectCodeObservationTests(unittest.TestCase):
    def setUp(self):
        self.original_hash = hashlib.sha256(ORIGINAL.read_bytes()).hexdigest()
        self.task = json.loads(ORIGINAL.read_text())
        self.source = self.task['source_records'][0]
        self.spec = copy.deepcopy(self.task['host_result']['evidence_specs'][0])
        self.spec.pop('identity')
        self.spec['direct_code_reference'] = direct_code_reference(self.task, self.source)
        self.assertIsNotNone(self.spec['direct_code_reference'])

    def tearDown(self):
        self.assertEqual(hashlib.sha256(ORIGINAL.read_bytes()).hexdigest(), self.original_hash)

    def candidate(self, task=None, source=None, spec=None):
        return candidate_evidence(task or self.task, source or self.source, spec or self.spec)

    def test_real_quote_is_admitted_without_invented_mapping_dates(self):
        item = self.candidate()
        self.assertEqual(item['source_check'], '通过')
        self.assertEqual(item['applicability_check'], '待核验')
        self.assertEqual(item['source_receipt']['version'], 3)
        self.assertEqual(item['identity']['mode'], 'direct_provider_observation')
        self.assertNotIn('valid_from', item['identity'])
        self.assertNotIn('valid_to', item['identity'])
        self.assertIsNone(item['published_at'])

    def test_all_61_original_cells_keep_exact_provider_values(self):
        for original in self.task['host_result']['evidence_specs']:
            spec = copy.deepcopy(original)
            spec.pop('identity')
            spec['direct_code_reference'] = self.spec['direct_code_reference']
            item = self.candidate(spec=spec)
            row = spec['record_locator']['row']
            table = spec['record_locator']['table']
            field = spec['record_locator']['field']
            self.assertEqual(item['raw_value'], self.source['snapshot']['tables'][table]['table'][field][row])

    def test_missing_reference_is_not_automatically_repaired(self):
        spec = copy.deepcopy(self.task['host_result']['evidence_specs'][0])
        with self.assertRaises(ContractError):
            self.candidate(spec=spec)

    def test_formats_cannot_be_mixed_even_with_null_mapping(self):
        for field, value in [('identity', None), ('identity', {}),
                             ('identity_basis_reference', {'version':1, 'evidence_id':'unknown'})]:
            with self.subTest(field=field, value=value):
                spec = dict(self.spec, **{field:value})
                with self.assertRaises(ContractError):
                    self.candidate(spec=spec)

    def test_historical_mode_has_no_direct_observation_reference(self):
        task = copy.deepcopy(self.task)
        task['research_mode'] = 'historical'
        task['input_hash'] = input_fingerprint(task)
        self.assertIsNone(direct_code_reference(task, task['source_records'][0]))
        with self.assertRaises(ContractError):
            self.candidate(task=task)

    def test_imported_snapshot_or_unconfirmed_transport_cannot_use_reference(self):
        for key in ('acquisition',):
            task = copy.deepcopy(self.task)
            task['source_records'][0].pop(key)
            self.assertIsNone(direct_code_reference(task, task['source_records'][0]))
            with self.assertRaises(ContractError):
                self.candidate(task=task, source=task['source_records'][0])
        task = copy.deepcopy(self.task)
        dispatch = task['source_records'][0]['acquisition']['dispatch_index']
        task['ledger']['dispatches'][dispatch]['network_worker_reaped'] = False
        self.assertIsNone(direct_code_reference(task, task['source_records'][0]))

    def test_name_only_input_and_model_added_code_do_not_qualify(self):
        task = copy.deepcopy(self.task)
        task['original_input']['question'] = task['question'] = '研究沪深300ETF'
        task['original_input']['objects'] = ['沪深300ETF']
        task['objects'] = ['沪深300ETF', '510300.SH']
        task['input_hash'] = input_fingerprint(task)
        self.assertIsNone(direct_code_reference(task, task['source_records'][0]))
        with self.assertRaises(ContractError):
            self.candidate(task=task)

    def test_changed_or_cross_task_reference_is_rejected(self):
        for field, value in [('task_id','another-task'), ('research_version',999),
                             ('snapshot_hash','0'*64), ('request_hash','0'*64),
                             ('declaration_hash','0'*64), ('version',True), ('research_version',True)]:
            with self.subTest(field=field):
                spec = copy.deepcopy(self.spec)
                spec['direct_code_reference'][field] = value
                with self.assertRaises(ContractError):
                    self.candidate(spec=spec)

    def test_alias_wrong_code_and_shortened_code_are_rejected(self):
        for obj in ('沪深300ETF','510500.SH','510300'):
            with self.subTest(obj=obj):
                with self.assertRaises(ContractError):
                    self.candidate(spec=dict(self.spec, object=obj))

    def test_mutated_response_and_request_do_not_reuse_live_receipt(self):
        for change in ('response','request'):
            with self.subTest(change=change):
                task = copy.deepcopy(self.task)
                source = task['source_records'][0]
                if change == 'response':
                    source['snapshot']['errorcode'] = -4001
                else:
                    source['parameters']['codes'] = '510500.SH'
                self.assertIsNone(direct_code_reference(task, source))
                with self.assertRaises(ContractError):
                    self.candidate(task=task, source=source)

    def test_snapshot_without_unique_retained_record_does_not_qualify(self):
        for records in ([], [self.source,self.source]):
            with self.subTest(count=len(records)):
                task = copy.deepcopy(self.task)
                task['source_records'] = copy.deepcopy(records)
                self.assertIsNone(direct_code_reference(task, self.source))

    def test_forecast_cannot_become_observed_market_fact(self):
        with self.assertRaises(ContractError):
            self.candidate(spec=dict(self.spec, nature='管理层展望'))

    def test_availability_and_publication_are_not_optional_or_invented(self):
        for change in ({'availability':None}, {'published_at':self.source['obtained_at']}):
            with self.subTest(change=change):
                with self.assertRaises(ContractError):
                    self.candidate(spec=dict(self.spec, **change))

    def test_direct_identity_cannot_smuggle_a_mapping_period(self):
        item = self.candidate()
        item['identity']['valid_from'] = '1900-01-01'
        with self.assertRaises(ContractError):
            validate_direct_code_identity(item, self.task, self.source)

    def test_analyzer_preserves_direct_observation_limitations(self):
        item = ResearchAnalyzer().assess(self.task, self.candidate(), self.spec['review'], self.task['source_records'])
        self.assertEqual(item['applicability_check'], '通过')
        self.assertIn(DIRECT_CODE_LIMITATION, item['applicability_review']['conditions'])
        self.task['evidence'].append(item)
        validate_provenance(item, self.task)

    def test_archive_restore_rechecks_reference_without_changing_ledger(self):
        item = ResearchAnalyzer().assess(self.task, self.candidate(), self.spec['review'], self.task['source_records'])
        self.task['evidence'].append(item)
        ledger = copy.deepcopy(self.task['ledger'])
        with tempfile.TemporaryDirectory() as folder:
            archive = ResearchArchive(folder)
            self.task['revision'] = 0  # New private store, not an overwrite of the original task.
            archive.save(self.task)
            restored = ResearchArchive(folder).get(self.task['id'])
            validate_provenance(restored['evidence'][0], restored)
            self.assertEqual(restored['ledger'], ledger)

    def test_model_contract_exposes_actual_reference_without_adoption(self):
        before = copy.deepcopy(self.task)
        contract = evidence_contract(self.task)
        source = next(item for item in contract['sources'] if item['artifact_id']==self.source['id'])
        self.assertEqual(source['direct_code_reference'], self.spec['direct_code_reference'])
        self.assertIn('direct_code_reference', contract['allowed_fields'])
        self.assertEqual(API_EXTRACTION_GUIDANCE_VERSION, 7)
        self.assertEqual(self.task, before)

    def test_an_old_reference_cannot_survive_scope_change(self):
        task = copy.deepcopy(self.task)
        task['objects'].append('510500.SH')
        task['input_hash'] = input_fingerprint(task)
        with self.assertRaises(ContractError):
            self.candidate(task=task)

    def test_calculation_plan_growth_does_not_change_user_code_reference(self):
        before = copy.deepcopy(self.spec['direct_code_reference'])
        self.task['calculation_specs'].append({'id':'new_binding','method':'observed_price_return_v1'})
        self.assertNotEqual(self.task['input_hash'], input_fingerprint(self.task))
        self.assertEqual(direct_code_reference(self.task,self.source),before)
        self.assertEqual(self.candidate()['source_check'],'通过')

    def test_actual_analysis_failure_is_repaired_without_mutating_its_record(self):
        file = ROOT/'result/acceptance/installed-natural-research-2ag6j62w/task.json'
        before = hashlib.sha256(file.read_bytes()).hexdigest()
        task = json.loads(file.read_text())
        self.assertEqual(task['status'],'失败')
        self.assertEqual(len(task['calculations']),1)
        self.assertNotEqual(task['input_hash'],input_fingerprint(task))
        for item in task['evidence']:
            validate_provenance(item,task)
        self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(),before)

    def test_strict_schema_preserves_explicit_direct_reference(self):
        # A synthetic one-position schema projection tests wire syntax only.
        # It does not assert that the real 61-row response was fully exposed.
        contract = evidence_contract(self.task)
        contract['sources'][0]['api_cells']['cells'] = [next(cell for cell in contract['sources'][0]['api_cells']['cells']
            if cell['proposal_fields']['record_locator'] == self.spec['record_locator'])]
        contract['sources'][0]['api_cells']['scan_complete'] = True
        with patch('researchhelper.reasoning.evidence_contract', return_value=contract):
            schema = api_extraction_output_schema(self.task, [{'kind':'api'}])
        self.assertIsNotNone(schema)
        row = {key:copy.deepcopy(self.spec[key]) for key in ('id','artifact_id','object','record_locator',
            'nature','question_ids','api_value_reference','availability','direct_code_reference')}
        row['publication_mode'] = 'unknown_current_snapshot'
        row['review'] = {key:copy.deepcopy(self.spec['review'][key]) for key in ('question_ids','allowed_natures','conditions','reason')}
        row['review']['evidence_need_texts'] = []
        decoded = decode_api_extraction_output({'version':1, 'evidence_specs':[row]}, schema)
        self.assertEqual(decoded['evidence_specs'][0]['direct_code_reference'], self.spec['direct_code_reference'])
        self.assertNotIn('identity', decoded['evidence_specs'][0])
        self.assertIsNone(decoded['evidence_specs'][0]['published_at'])

    def test_strict_schema_does_not_invent_a_mapping_if_reference_is_absent(self):
        contract = evidence_contract(self.task)
        contract['sources'][0]['api_cells']['scan_complete'] = True
        contract['sources'][0].pop('direct_code_reference')
        with patch('researchhelper.reasoning.evidence_contract', return_value=contract):
            self.assertIsNone(api_extraction_output_schema(self.task, [{'kind':'api'}]))

    def test_strict_planning_uses_actual_filing_filter_choices(self):
        shape = next(item for item in _planned_request_schemas(self.task)
            if item['properties']['operation']['enum']==['report_query'])
        parameter = shape['properties']['parameters']['properties']['functionpara']
        self.assertTrue(_matches({}, parameter))
        self.assertTrue(_matches({'reportType':'901'}, parameter))
        self.assertFalse(_matches({'mode':'allFund'}, parameter))
        self.assertFalse(_matches('901', parameter))

    def test_normal_binding_prompt_has_no_conflicting_strict_version_field(self):
        captured = []
        def respond(task,messages,phase):
            captured.append(json.loads(messages[-1]['content']))
            return json.dumps({'calculations':[],'reason':'仅检查容器，不执行计算'})
        model = SimpleNamespace(reason_phase=respond,supports_structured_analysis=lambda:False)
        wire = {'type':'object','properties':{'version':{'type':'integer','enum':[1]},
            'calculations':{'type':'array','items':{}},'reason':{'type':'string'}},
            'required':['version','calculations','reason'],'additionalProperties':False}
        payload = {'binding_schema':copy.deepcopy(wire)}
        model_object(model,self.task,'bind_calculations',payload,{'calculations','reason'})
        self.assertEqual(set(captured[0]['binding_schema']['properties']),{'calculations','reason'})
        self.assertEqual(captured[0]['binding_schema']['required'],['calculations','reason'])
        self.assertEqual(payload['binding_schema'],wire)

    def test_numeric_reference_candidates_use_actual_ids_and_decoder_paths(self):
        task = json.loads((ROOT/'result/acceptance/installed-natural-research-2ag6j62w/task.json').read_text())
        contract = analysis_contract(task)
        candidates = [item for question in contract['by_question'].values() for item in question['numeric_quantity_references']]
        self.assertTrue(candidates)
        self.assertTrue(any(item['source']=='calculation' for item in candidates))
        ids = {item['id'] for item in task['evidence']+task['calculations']}
        for item in candidates:
            self.assertEqual(set(item),{'source','ref','path','format','digits'})
            self.assertIn(item['ref'],ids)
            self.assertNotIn('output.',item['path'])

    def test_only_old_rejected_binding_checkpoint_expires_on_protocol_upgrade(self):
        task = {'run_mode':'model','reasoning_state':{'initial_calculation_binding':{'version':1,'rejected':True}}}
        self.assertTrue(ResearchAgent._binding_checkpoint_needs_upgrade(task,{'id':'calculation'}))
        self.assertFalse(ResearchAgent._binding_checkpoint_needs_upgrade(task,{'id':'evidence'}))
        for version in (2,999,True):
            task['reasoning_state']['initial_calculation_binding']['binding_prompt_version'] = version
            self.assertFalse(ResearchAgent._binding_checkpoint_needs_upgrade(task,{'id':'calculation'}))
        task['reasoning_state']['initial_calculation_binding'] = {'version':1,'rejected':False}
        self.assertFalse(ResearchAgent._binding_checkpoint_needs_upgrade(task,{'id':'calculation'}))


if __name__ == '__main__':
    unittest.main()
