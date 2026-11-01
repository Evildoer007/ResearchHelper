"""Independent missing-file regressions; all payload variants are synthetic.

The unchanged complete official fixture anchors existing file identities. No
live request, invented original, financial adoption or study completion.
"""
import copy
import base64
import hashlib
import json
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import ContractError
from researchhelper.index_sources import csi_catalogue_contents,parse_csi_catalogue

FIXTURE=ROOT/'result/acceptance/csi-official-http-x0_w9mw9/detail.json'


class PartialIndexCatalogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw=FIXTURE.read_bytes();cls.digest=hashlib.sha256(cls.raw).hexdigest()
        cls.original=json.loads(cls.raw)

    def setUp(self):
        self.offline=patch('socket.socket.connect',side_effect=AssertionError('Offline fixture only'))
        self.offline.start();self.addCleanup(self.offline.stop)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),self.digest)

    def view(self,role,value):
        payload=copy.deepcopy(self.original)
        if value=='absent':payload['data'].pop(role,None)
        else:payload['data'][role]=value
        return csi_catalogue_contents(json.dumps(payload).encode(),'000300.SH')

    def test_complete_existing_entry_ids_and_shape_remain_unchanged(self):
        view=csi_catalogue_contents(self.raw,'000300.SH')
        self.assertEqual(view['issues'],[])
        self.assertEqual(view['entries'],parse_csi_catalogue(self.raw,'000300.SH'))
        self.assertEqual([x['role'] for x in view['entries']],['weights','members'])

    def test_missing_members_does_not_block_weights_and_converse(self):
        for label,kept in [('样本列表','weights'),('样本权重','members')]:
            for value in ('absent',None,[]):
                with self.subTest(label=label,value=value):
                    view=self.view(label,value)
                    self.assertEqual([x['role'] for x in view['entries']],[kept])
                    self.assertEqual(view['issues'][0]['status'],'not_advertised')

    def test_ambiguous_role_is_not_resolved_by_picking_first_or_blocking_other(self):
        entries=self.original['data']['样本列表']
        for value in (entries+entries,{},'bad'):
            with self.subTest(value=value):
                view=self.view('样本列表',value)
                self.assertEqual([x['role'] for x in view['entries']],['weights'])
                self.assertEqual(view['issues'][0]['status'],'ambiguous')

    def test_bad_format_or_address_is_skipped_without_exposing_untrusted_link(self):
        for change,status in [('fileType','unsupported_format'),('filePath','invalid_address')]:
            rows=copy.deepcopy(self.original['data']['样本列表']);rows[0][change]='https://invalid.example/private'
            view=self.view('样本列表',rows)
            self.assertEqual([x['role'] for x in view['entries']],['weights'])
            self.assertEqual(view['issues'][0]['status'],status)
            self.assertNotIn('invalid.example',str(view))

    def test_empty_success_is_preserved_as_no_choices_not_full_coverage(self):
        payload=copy.deepcopy(self.original);payload['data']={}
        view=csi_catalogue_contents(json.dumps(payload).encode(),'000300.SH')
        self.assertEqual(view['entries'],[])
        self.assertEqual(len(view['issues']),2)

    def test_bad_transport_or_unknown_index_still_fails(self):
        for raw in (b'null',b'{"code":"500","success":false,"data":{}}',b'not json'):
            with self.subTest(raw=raw),self.assertRaises(ContractError):
                csi_catalogue_contents(raw,'000300.SH')
        with self.assertRaises(ContractError):csi_catalogue_contents(self.raw,'unknown')


class PartialIndexCatalogueRuntimeTests(unittest.TestCase):
    def setUp(self):
        from tests.test_csi_official_source import CSIOfficialSourceTests
        CSIOfficialSourceTests.setUpClass()
        CSIOfficialSourceTests.setUp(self)
        self.raw_file=CSIOfficialSourceTests.raw_file
        payload=json.loads(CSIOfficialSourceTests.raw_catalogue)
        payload['data'].pop('样本列表')
        self.raw_catalogue=json.dumps(payload).encode()

    def wire(self,task,selection,started,timeout,event,**kwargs):
        from researchhelper.index_sources import parse_csi_workbook
        raw=self.raw_catalogue if selection['role']=='catalog' else self.raw_file
        event.update(transport_isolated=True,network_request_started=True,network_worker_reaped=True)
        payload=dict(original_base64=base64.b64encode(raw).decode(),
            original_sha256=hashlib.sha256(raw).hexdigest(),url=selection['url'],response_headers={})
        if selection['role']=='catalog':
            contents=csi_catalogue_contents(raw,selection['index_code'])
            payload.update(entries=contents['entries'],catalogue_issues=contents['issues'])
        else:
            payload['parsed']=parse_csi_workbook(raw,selection['index_code'],selection['role'])
        return payload

    def catalogue(self):
        with patch('researchhelper.source_transport.official_index',side_effect=self.wire):
            record=self.collector.external(self.task,'official','index_catalog',dict(index_code='000300.SH'),{})
        self.task['source_records'].append(record)
        return record

    def test_weights_remain_downloadable_and_missing_members_remain_visible(self):
        from researchhelper.index_sources import official_index_shapes,official_index_source_view
        record=self.catalogue();entry=record['snapshot']['entries'][0]
        shapes=official_index_shapes(self.task)
        choices=next(s for s in shapes if s['operation']=='index_file')['official_index_file_choices']
        self.assertEqual([item['role'] for item in choices],['weights'])
        view=official_index_source_view(record)
        self.assertEqual(view['catalogue_issues'][0]['role'],'members')
        self.assertFalse(view['financial_approval'])
        with patch('researchhelper.source_transport.official_index',side_effect=self.wire):
            file=self.collector.external(self.task,'official','index_file',
                dict(catalog_id=record['id'],entry_id=entry['id'],as_of='2026-10-19'),{})
        self.assertEqual(file['snapshot']['parsed']['row_count'],300)
        self.assertEqual(self.task['ledger']['used']['data_requests'],2)
        self.assertEqual(self.task['ledger']['used']['model_calls'],0)
        self.assertEqual(self.task['evidence'],[])

    def test_modified_gap_or_unadvertised_selection_never_bypasses_original(self):
        from researchhelper.index_sources import validate_official_index_request
        record=self.catalogue();entry=record['snapshot']['entries'][0]
        params=dict(catalog_id=record['id'],entry_id=entry['id'],as_of=None)
        before=copy.deepcopy(self.task['ledger'])
        with self.assertRaises(ContractError):
            validate_official_index_request(self.task,'index_file',dict(params,entry_id='invented_missing_members'))
        record['snapshot']['catalogue_issues']=[]
        with self.assertRaises(ContractError):validate_official_index_request(self.task,'index_file',params)
        self.assertEqual(self.task['ledger'],before)


if __name__=='__main__':unittest.main()
