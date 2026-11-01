"""Task-bound official pagination, with no model or network calls.

Replay the closed homepage bytes; supplementary catalogue rows are synthetic
protocol fixtures. No research is reopened and no financial fact is approved.
"""
import base64
import copy
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import ContractError,fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.archive import ResearchArchive
from researchhelper.planning import input_fingerprint
from researchhelper.reports import compose
from researchhelper.official_sources import (catalogue_pagination,request_shapes,validate_request,
    canonical_request_parameters,release_choices,parse_catalogue,catalogue_url,
    _pagination_declaration,_is_catalogue_address)
from researchhelper.output_schema import _planned_request_schemas,_matches,decode_acquisition_output,acquisition_output_schema,_decimal_page_pattern

FIXTURE=ROOT/'result/acceptance/one-sentence-research-1cr8hfby/task.json'


class DeclaredOfficialPaginationTests(unittest.TestCase):
    def setUp(self):
        raw=FIXTURE.read_bytes();self.before=hashlib.sha256(raw).hexdigest()
        self.task=json.loads(raw);self.task['official_catalogue_pagination_version']=1
        self.guard=patch('socket.socket.connect',side_effect=AssertionError('Offline only'))
        self.guard.start();self.addCleanup(self.guard.stop)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),self.before)

    def test_real_homepage_declares_bounds_without_financial_or_completeness_approval(self):
        before=copy.deepcopy(self.task);view=catalogue_pagination(self.task)
        self.assertEqual(view['page_count'],34)
        self.assertEqual(view['maximum_page'],33)
        self.assertFalse(view['automatic_traversal'] or view['completeness_verified'] or view['financial_approval'])
        self.assertEqual(validate_request(self.task,'release_catalog',dict(channel='nbs_release',page='4')),
            'https://www.stats.gov.cn/sj/zxfbhjd/index_4.html')
        self.assertEqual(self.task,before)

    def test_changed_or_unmetered_or_foreign_homepage_does_not_expand_pages(self):
        for change in ('foreign','hash','dispatch'):
            task=copy.deepcopy(self.task)
            if change=='foreign':task['id']='different-task'
            elif change=='hash':task['source_records'][0]['hash']='changed'
            else:
                for event in task['ledger']['dispatches']:event.pop('network_worker_reaped',None)
            with self.subTest(change=change):
                self.assertIsNone(catalogue_pagination(task))
                with self.assertRaises(ContractError):validate_request(task,'release_catalog',dict(channel='nbs_release',page='4'))

    def test_out_of_bounds_and_arbitrary_paths_are_rejected(self):
        for page in ('34','-1','04','1.0','../../private',True,4):
            with self.subTest(page=page),self.assertRaises(ContractError):
                validate_request(self.task,'release_catalog',dict(channel='nbs_release',page=page))
        for url in ('http://www.stats.gov.cn/sj/zxfbhjd/index_4.html',
            'https://elsewhere.invalid/sj/zxfbhjd/index_4.html','https://www.stats.gov.cn/sj/zxfbhjd/index_04.html',
            'https://www.stats.gov.cn/sj/zxfbhjd/index_4.html?token=none'):
            self.assertFalse(_is_catalogue_address(url))

    def test_only_literal_top_level_declaration_is_used_without_executing_script(self):
        valid=b'<script>\ncreatePageHTML(34, 0, "index", "html");\n</script>'
        self.assertEqual(_pagination_declaration(valid),(34,0))
        for text in ('/*\ncreatePageHTML(34, 0, "index", "html");\n*/',
            'function unused(){\ncreatePageHTML(34, 0, "index", "html");\n}',
            '`\ncreatePageHTML(34, 0, "index", "html");\n`',
            'createPageHTML(34, 0, "other", "html");',
            'createPageHTML(34, 0, "index", "html");\ncreatePageHTML(35, 0, "index", "html");'):
            with self.subTest(text=text):self.assertIsNone(_pagination_declaration(('<script>\n'+text+'\n</script>').encode()))

    def test_new_wire_uses_integer_bounds_and_decodes_to_unchanged_provider_parameters(self):
        contract=dict(tools=request_shapes(self.task),question_ids=['q1'],callable_codes=[])
        schema=acquisition_output_schema(self.task,dict(data_request_contract=contract))
        wire=dict(version=1,binding_ref=schema['properties']['binding_ref']['enum'][0],stop_reason='',
            data_requests=[dict(id='older_page',source='official',operation='release_catalog',
                parameters=dict(channel='nbs_release',page=4),question_ids=['q1'],selection_reason='核对本任务较早期的官方发布')])
        decoded=decode_acquisition_output(wire,schema,contract)
        self.assertEqual(decoded['data_requests'][0]['parameters'],dict(channel='nbs_release',page='4'))
        self.assertEqual(wire['data_requests'][0]['parameters']['page'],4)
        self.assertEqual(canonical_request_parameters(self.task,'release_catalog',dict(channel='nbs_release',page=4)),
            dict(channel='nbs_release',page='4'))
        wire['data_requests'][0]['parameters']['page']=34
        self.assertFalse(_matches(wire,schema))

    def test_legacy_page_limits_and_snapshot_parsing_are_unchanged(self):
        old=copy.deepcopy(self.task);old.pop('official_catalogue_pagination_version')
        with self.assertRaises(ContractError):validate_request(old,'release_catalog',dict(channel='nbs_release',page='4'))
        with self.assertRaises(ContractError):catalogue_url('4')
        root=next(record for record in old['source_records'] if record.get('parameters')==dict(channel='nbs_release',page='0'))
        self.assertEqual(parse_catalogue(base64.b64decode(root['snapshot']['original_base64']),old['cutoff']),root['snapshot'])

    def test_decimal_range_is_exact_without_enumerating_pages(self):
        import re
        for maximum in (0,3,9,10,33,100,399,999,1000,345678901):
            pattern=_decimal_page_pattern(maximum)
            for number in (0,1,maximum,max(0,maximum-1),maximum+1,maximum+10):
                with self.subTest(maximum=maximum,number=number):
                    self.assertEqual(re.fullmatch(pattern,str(number)) is not None,number<=maximum)
            for invalid in ('00','01','-1','1.0',' 1','1e1'):
                self.assertIsNone(re.fullmatch(pattern,invalid))
            self.assertLess(len(pattern),500)

    def test_retained_later_catalogue_candidates_are_not_hidden_by_first_hundred(self):
        for page in range(2,7):
            payload=''.join(f'<a href="https://www.stats.gov.cn/sj/zxfbhjd/202609/t20260901_{page*100+i}.html">合成条目{page}-{i}</a>' for i in range(25)).encode()
            snapshot=parse_catalogue(payload,self.task['cutoff']);params=dict(channel='nbs_release',page=str(page))
            digest=fingerprint(dict(source='official',operation='release_catalog',parameters=params))
            record=dict(id='synthetic-page-'+str(page),kind='api',source='official',operation='release_catalog',
                parameters=params,task_id=self.task['id'],research_version=self.task['version'],
                status='成功待核验',snapshot=snapshot,hash=fingerprint(snapshot),request_hash=digest)
            self.task['source_records'].append(record)
            self.task['ledger']['dispatches'].append(dict(kind='data_requests',source='official',operation='release_catalog',
                fingerprint=digest,response_snapshot_hash=record['hash'],status='成功',
                transport_isolated=True,network_request_started=True,network_worker_reaped=True))
        choices=release_choices(self.task)
        self.assertGreater(len(choices),100)
        self.assertTrue(any(choice['catalog_id']=='synthetic-page-6' for choice in choices))

    def test_unknown_policy_is_not_permission_and_new_tasks_bind_it(self):
        for value in (True,'1',2):
            with self.subTest(value=value),self.assertRaises(ContractError):
                catalogue_pagination(dict(self.task,official_catalogue_pagination_version=value))
        with tempfile.TemporaryDirectory() as directory:
            agent=ResearchAgent(ResearchArchive(directory),ROOT,require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task=agent.create('核对国家统计局较早期制造业PMI资料',research_mode='current')
            self.assertEqual(task['official_catalogue_pagination_version'],1)
            old=copy.deepcopy(task);old.pop('official_catalogue_pagination_version')
            self.assertNotEqual(input_fingerprint(task),input_fingerprint(old))
            agent._adopt_method_knowledge(task)
            task['gaps']=['离线契约检查，尚未执行研究']
            self.assertEqual(compose(task)['official_catalogue_pagination_version'],1)
