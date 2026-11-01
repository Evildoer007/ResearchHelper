"""Official CSI contracts, with immutable real binary fixtures and negative cases."""
import base64
import copy
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xlrd
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ContractError
from researchhelper.evidence import EvidenceCollector,current_query_reference,current_snapshot_reference,data_request_contract
from researchhelper.index_sources import (parse_csi_catalogue,parse_csi_workbook,validate_csi_url,
    validate_official_index_request,official_index_source_view)
from researchhelper.output_schema import acquisition_output_schema

CATALOGUE=ROOT/'result/acceptance/csi-official-http-x0_w9mw9/detail.json'
FILE=ROOT/'result/acceptance/csi-official-files-k3mznoa2/000300closeweight.xls'


class CSIOfficialSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw_catalogue=CATALOGUE.read_bytes();cls.raw_file=FILE.read_bytes()
        book=xlrd.open_workbook(file_contents=cls.raw_file,on_demand=True);sheet=book.sheet_by_index(0)
        cls.rows=[sheet.row_values(i) for i in range(sheet.nrows)]
        cls.types=[list(sheet.row_types(i)) for i in range(sheet.nrows)]
        cls.sheet_name=sheet.name;book.release_resources()

    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.agent=ResearchAgent(ResearchArchive(Path(temporary.name)),ROOT,offline=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task=self.agent.create('获取沪深300在2026年10月19日的完整成分权重',research_mode='current')
        self.collector=EvidenceCollector(offline=False)

    def wire(self,task,selection,started,timeout,event,**kwargs):
        # Offline transport fixture, never a real provider-permission receipt.
        raw=self.raw_catalogue if selection['role']=='catalog' else self.raw_file
        event.update(transport_isolated=True,network_request_started=True,network_worker_reaped=True)
        payload=dict(original_base64=base64.b64encode(raw).decode(),original_sha256=hashlib.sha256(raw).hexdigest(),
            url=selection['url'],response_headers={})
        if selection['role']=='catalog':payload['entries']=parse_csi_catalogue(raw,selection['index_code'])
        else:payload['parsed']=parse_csi_workbook(raw,selection['index_code'],selection['role'])
        return payload

    def catalog(self):
        record=self.collector.external(self.task,'official','index_catalog',{'index_code':'000300.SH'},{})
        self.task['source_records'].append(record);return record

    def test_real_cells_preserve_all_members_leading_zeros_unit_and_raw_sum(self):
        data=parse_csi_workbook(self.raw_file,'000300.SH','weights')
        self.assertEqual(data['row_count'],300);self.assertEqual(data['unique_members'],300)
        self.assertEqual(data['weight_sum'],'100.001');self.assertEqual(data['unit'],'%')
        self.assertEqual(data['observation_date'],'2026-10-19')
        self.assertEqual(data['members'][0]['raw_code'],'000001')
        self.assertEqual(data['members'][0]['code'],'000001.SZ')
        self.assertFalse(data['financial_approval'] or data['historical_availability_verified'])

    def fake_book(self,rows,types):
        sheet=SimpleNamespace(name=self.sheet_name,nrows=len(rows),ncols=len(rows[0]),
            row_values=lambda i:rows[i],cell_type=lambda r,c:types[r][c])
        return SimpleNamespace(nsheets=1,sheet_by_index=lambda i:sheet,release_resources=lambda:None)

    def test_missing_duplicate_foreign_date_unit_bool_and_numeric_codes_are_rejected(self):
        for change in ('missing','duplicate','mixed_date','unit','bool','numeric_code','negative','nonfinite'):
            rows=copy.deepcopy(self.rows);types=copy.deepcopy(self.types)
            if change=='missing':rows.pop();types.pop()
            elif change=='duplicate':rows[2][4]=rows[1][4];rows[2][7:9]=rows[1][7:9]
            elif change=='mixed_date':rows[2][0]='20261020'
            elif change=='unit':rows[0][9]='权重'
            elif change=='bool':rows[2][9]=1;types[2][9]=xlrd.XL_CELL_BOOLEAN
            elif change=='numeric_code':rows[1][4]=1;types[1][4]=xlrd.XL_CELL_NUMBER
            elif change=='negative':rows[1][9]=-1
            else:rows[1][9]=float('nan')
            with self.subTest(change=change),patch('xlrd.open_workbook',return_value=self.fake_book(rows,types)),self.assertRaises(ContractError):
                parse_csi_workbook(self.raw_file,'000300.SH','weights')

    def test_only_exact_official_directory_links_are_allowed(self):
        entries=parse_csi_catalogue(self.raw_catalogue,'000300.SH');self.assertEqual(len(entries),2)
        url=entries[0]['url']
        for bad in (url.replace('https:','http:'),url.replace('oss-ch.csindex.com.cn','evil.example'),
                    url.replace('.com.cn/','.com.cn:444/'),url+'#fragment',url.replace('000300closeweight','000905closeweight')):
            with self.subTest(url=bad),self.assertRaises(ContractError):validate_csi_url(bad,'000300.SH','weights')

    def test_catalogue_and_file_have_two_budgeted_requests_not_financial_approval(self):
        with patch('researchhelper.source_transport.official_index',side_effect=self.wire):
            catalogue=self.catalog();entry=catalogue['snapshot']['entries'][0]
            params=dict(catalog_id=catalogue['id'],entry_id=entry['id'],as_of='2026-10-19')
            record=self.collector.external(self.task,'official','index_file',params,{})
            self.task['source_records'].append(record)
        self.assertEqual(self.task['ledger']['used']['data_requests'],2)
        self.assertIsNotNone(current_query_reference(self.task,record));self.assertIsNone(current_snapshot_reference(self.task,record))
        shown=official_index_source_view(record)
        self.assertEqual(len(shown['workbook']['raw_rows']),300)
        self.assertNotIn('original_base64',shown);self.assertTrue(shown['original_archive_ref']['binary_retained'])

    def test_wrong_asof_preserves_original_without_relabel_or_repeat(self):
        with patch('researchhelper.source_transport.official_index',side_effect=self.wire) as transport:
            catalogue=self.catalog();entry=catalogue['snapshot']['entries'][0]
            params=dict(catalog_id=catalogue['id'],entry_id=entry['id'],as_of='2026-10-16')
            record=self.collector.external(self.task,'official','index_file',params,{})
            self.task['source_records'].append(record)
            self.assertNotEqual(record['status'],'成功待核验')
            self.assertEqual(record['snapshot']['parsed']['observation_date'],'2026-10-19')
            self.assertIsNone(current_query_reference(self.task,record))
            with self.assertRaises(ContractError):self.collector.external(self.task,'official','index_file',dict(params,as_of='2026-10-19'),{})
            self.assertEqual(transport.call_count,2)

    def test_task_bindings_and_actual_parameter_shapes_are_checked_before_dispatch(self):
        with patch('researchhelper.source_transport.official_index',side_effect=self.wire):catalogue=self.catalog()
        entry=catalogue['snapshot']['entries'][0];params=dict(catalog_id=catalogue['id'],entry_id=entry['id'],as_of=None)
        other=self.agent.create('获取沪深300完整权重',research_mode='current');other['source_records']=[catalogue]
        with self.assertRaises(ContractError):validate_official_index_request(other,'index_file',params)
        catalogue['snapshot']['entries'][0]['url']='https://evil.example/a.xls'
        with self.assertRaises(ContractError):validate_official_index_request(self.task,'index_file',params)

    def test_planning_schema_exposes_only_actual_file_ids_not_a_free_url(self):
        with patch('researchhelper.source_transport.official_index',side_effect=self.wire):self.catalog()
        contract=data_request_contract(self.task);schema=acquisition_output_schema(self.task,{'data_request_contract':contract})
        variant=next(v for v in schema['properties']['data_requests']['items']['anyOf']
            if v['properties']['operation']['enum']==['index_file'])
        self.assertNotIn('url',str(variant['properties']['parameters']['properties'].keys()))
        self.assertEqual(set(variant['properties']['parameters']['properties']),{'catalog_id','entry_id','as_of'})


if __name__=='__main__':unittest.main()
