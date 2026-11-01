"""Explicit source alternatives, separate from finance and original approval."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ContractError,fingerprint
from researchhelper.planning import input_fingerprint,question_specs
from researchhelper.evidence import (snapshot_record,_record_current_acquisition,identity_query_parameters,
    IDENTITY_CANDIDATE_PROFILE,IFIND_REPORT_FIELDS,data_request_contract)
from researchhelper.source_requirements import (alternatives,validate_choice,register,checked_entries,
    frozen_requirements,REGISTRY,FIELD,POLICY)
from researchhelper.acquisition import initial_acquisition_coverage
from researchhelper.output_schema import acquisition_output_schema
from researchhelper.reasoning import validate_plan


class CatalogueAlternativeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.agent=ResearchAgent(ResearchArchive(Path(temporary.name)),ROOT,offline=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task=self.create()
        self.name=self.retain_name(self.task)
        self.ref=alternatives(self.task)[0]['reference']
        self.needs=[self.need('a','600030.SH'),self.need('h','6030.HK')]

    def create(self,question='核对中信证券半年报营业收入',kind='fact',objects=None,extra=None):
        questions=[dict(id='q1',question=question,kind=kind,evidence_needs=['半年报营业收入'])]
        if extra:questions.append(extra)
        return self.agent.create(question,entry='个股',research_mode='current',objects=objects or [],questions=questions)

    def retain(self,task,operation,params,payload):
        source=snapshot_record(task,'ifind',operation,params,payload)
        event=dict(kind='data_requests',source='ifind',operation=operation,status='成功',batch=task['batch'],
            fingerprint=source['request_hash'],transport_isolated=True,network_request_started=True,network_worker_reaped=True)
        task['ledger']['dispatches'].append(event)
        _record_current_acquisition(task,source,event);task['source_records'].append(source)
        return source

    def retain_name(self,task):
        return self.retain(task,'get_thscode',identity_query_parameters('secname','中信证券',IDENTITY_CANDIDATE_PROFILE),
            dict(errorcode=0,inputParams=dict(mode='secname'),
                tables=[dict(secname='中信证券',table=dict(thscode=['600030.SH,6030.HK,CIIHY.PQ']))]))

    def need(self,ident,code,*,reference=True,questions=None):
        need=dict(id=ident,source='ifind',operation='report_query',parameters=dict(codes=code,functionpara={},
            beginrDate='2026-07-01',endrDate='2026-10-20',outputpara=','.join(k+':Y' for k in IFIND_REPORT_FIELDS)),
            question_ids=questions or ['q1'],selection_reason='独立离线目录需求协议检查')
        if reference:need[FIELD]=self.ref
        return need

    def retain_catalogues(self,*,unmarked_h=False):
        needs=copy.deepcopy(self.needs)
        if unmarked_h:needs[1].pop(FIELD)
        register(self.task,needs)
        records=[]
        for need in needs:
            code=need['parameters']['codes']
            row=dict(reportDate='2026-08-21',thscode=code,secName='中信证券',ctime=None,
                reportTitle='合成半年报目录条目',pdfURL='https://static.cninfo.com.cn/finalpage/2026-08-21/fixture.pdf',seq=need['id'])
            source=self.retain(self.task,'report_query',need['parameters'],dict(errorcode=0,tables=[row]))
            source.update(question_ids=list(need['question_ids']),data_need_ids=[need['id']]);records.append(source)
        return records

    def original(self,catalog,question_ids=None):
        original=dict(id='synthetic_readable_original',kind='filing',source='ifind',operation='filing_original',
            task_id=self.task['id'],research_version=self.task['version'],question_ids=question_ids or ['q1'],
            parameters=dict(catalog_id=catalog['id'],source_id='synthetic_original'))
        self.task['source_records'].append(original)
        return original

    def coverage(self):
        # Only the document-readiness seam is synthetic; no finance gate is invoked.
        views=[dict(id=r['id'],kind='filing') for r in self.task['source_records'] if r['kind']=='filing']
        with patch('researchhelper.acquisition.extractable_source_views',return_value=views), \
                patch('researchhelper.acquisition._with_method_source_coverage',side_effect=lambda task,views,result:result):
            return initial_acquisition_coverage(self.task)

    def test_choice_is_bound_to_real_candidate_codes_and_never_identity_or_equivalence(self):
        choice=alternatives(self.task)[0]
        self.assertEqual(choice['codes'],['600030.SH','6030.HK'])
        self.assertFalse(choice['identity_approved'] or choice['originals_equivalent'] or choice['financial_approval'])
        self.assertEqual(choice['candidate_artifact_id'],self.name['id'])
        self.assertEqual(validate_choice(self.task,self.needs[0]),choice)

    def test_one_explicit_alternative_original_finishes_source_readiness_not_the_research(self):
        catalogues=self.retain_catalogues();self.assertFalse(self.coverage()['ready'])
        self.original(catalogues[0]);coverage=self.coverage()
        self.assertTrue(coverage['ready']);self.assertEqual(coverage['pending_original_question_ids'],[])
        self.assertFalse(coverage['financial_approval']);self.assertFalse(self.task['evidence'])

    def test_unmarked_second_directory_keeps_its_independent_requirement(self):
        catalogues=self.retain_catalogues(unmarked_h=True);self.original(catalogues[0])
        self.assertFalse(self.coverage()['ready'])
        self.assertIn('q1',self.coverage()['pending_original_question_ids'])

    def test_missing_request_registration_or_dispatch_cannot_relax_a_directory(self):
        catalogues=self.retain_catalogues();self.original(catalogues[0])
        before=copy.deepcopy(self.task[REGISTRY]);self.task[REGISTRY]={}
        self.assertFalse(self.coverage()['ready']);self.task[REGISTRY]=before
        catalogues[1].pop('acquisition')
        self.assertFalse(self.coverage()['ready'])

    def test_explicit_markets_multiple_codes_and_comparisons_have_no_alternative_permission(self):
        for question,kind,objects in (('比较中信证券A股与H股半年报','comparison',None),
                ('核对中信证券H股半年报','fact',None),('核对中信证券半年报','fact',['600030.SH','6030.HK'])):
            task=self.create(question,kind,objects);self.retain_name(task)
            self.assertEqual(alternatives(task),[])
            with self.assertRaises(ContractError):validate_choice(task,self.needs[0])
        etfs=self.create('比较510300.SH和510500.SH费率','comparison',['510300.SH','510500.SH'])
        self.assertEqual(alternatives(etfs),[])

    def test_invalid_or_cross_task_reference_is_rejected_before_any_data_request(self):
        task=self.create();self.retain_name(task)
        for need in (dict(self.needs[0],**{FIELD:'invented'}),
                     dict(self.needs[0],parameters=dict(self.needs[0]['parameters'],codes='300750.SZ')),
                     dict(self.needs[0],operation='get_thscode')):
            with self.assertRaises(ContractError):validate_choice(self.task,need)
        with self.assertRaises(ContractError):validate_choice(task,self.needs[0])
        self.assertEqual(self.task['ledger']['used']['data_requests'],0)

    def test_persisted_intent_is_immutable_idempotent_and_survives_a_new_execution_batch(self):
        before=copy.deepcopy(self.task['ledger'])
        self.assertTrue(register(self.task,self.needs));self.assertFalse(register(self.task,self.needs))
        self.task['batch']+=1;self.assertEqual(len(checked_entries(self.task)),2)
        self.assertEqual(self.task['ledger'],before)
        packet=frozen_requirements(self.task);packet['requests'][0]['request']['id']='changed'
        self.assertNotEqual(packet,frozen_requirements(self.task))

    def test_tampered_intent_and_changed_input_do_not_become_approved_alternatives(self):
        register(self.task,self.needs);key=next(iter(self.task[REGISTRY]))
        self.task[REGISTRY][key]['request'][FIELD]='changed'
        with self.assertRaises(ContractError):checked_entries(self.task)
        self.task[REGISTRY]={};register(self.task,self.needs)
        self.task['version']+=1;self.task['input_hash']=input_fingerprint(self.task)
        with self.assertRaises(ContractError):checked_entries(self.task)

    def test_schema_and_both_preflights_keep_reference_out_of_provider_parameters(self):
        contract=data_request_contract(self.task)
        schema=acquisition_output_schema(self.task,dict(data_request_contract=contract))
        branch=next(b for b in schema['properties']['data_requests']['items']['anyOf']
                    if b['properties']['operation']['enum']==['report_query'])
        self.assertIn(FIELD,branch['properties']);self.assertNotIn(FIELD,branch['properties']['parameters']['properties'])
        self.assertIn(self.ref,str(branch['properties'][FIELD]))
        self.agent._validated_data_needs(self.task,self.needs)
        validate_plan(self.task,dict(questions=question_specs(self.task),data_requests=self.needs,calculations=[]))

    def test_legacy_and_unknown_policy_cannot_accept_a_new_reference(self):
        for value in (None,True,2,'1'):
            task=copy.deepcopy(self.task)
            if value is None:task.pop(POLICY)
            else:task[POLICY]=value
            with self.assertRaises(ContractError):validate_choice(task,self.needs[0])

    def test_an_original_for_another_question_cannot_cover_this_one(self):
        catalogues=self.retain_catalogues();self.original(catalogues[0],question_ids=['foreign_question'])
        self.assertFalse(self.coverage()['ready'])

    def test_registration_precedes_external_dispatch_in_the_shared_engine(self):
        self.task['status']='运行中'
        self.agent.archive.save(self.task)
        def external(*args,**kwargs):
            self.assertEqual(len(checked_entries(self.task)),2)
            raise ContractError('synthetic offline transport stop')
        with patch.object(self.agent,'_save_model_checkpoint'),patch.object(self.agent.collector,'external',side_effect=external) as call:
            self.agent._acquire_sources(self.task,self.needs)
        self.assertEqual(call.call_count,2)
        self.assertFalse(self.task['evidence'])
