"""Offline contracts for independent announcement and domestic policy routes."""
import base64
import copy
from datetime import datetime
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.announcement_sources import (parse_catalogue as parse_announcements,
    issuer_from_response, original_url, request_spec, validate_parameters, download)
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector, data_request_contract, acquisition_channels
from researchhelper.official_sources import (article_url, parse_catalogue, release_choices, validate_original,
    validate_request, canonical_request_parameters, selection_references)
from researchhelper.output_schema import _planned_request_schemas, _matches
from researchhelper.reasoning import source_views, extractable_source_views
from researchhelper.source_catalogue import source_catalogue

CUTOFF = '2026-10-17T18:00:00+08:00'
CN_ISSUER = json.dumps([dict(code='600519', orgId='gssh0600519', type='shj', category='A股', zwjc='贵州茅台')]).encode()
HK_ISSUER = json.dumps([dict(i=7609, c='00700', n='騰訊控股', s=16790),dict(i=1,c='00001',n='長和',s=6592)]).encode()
CN_ROW = dict(secCode='600519', secName='贵州茅台', orgId='gssh0600519', announcementId='fixture001',
    announcementTitle='测试公告，非真实披露', announcementTime=int(datetime.fromisoformat('2026-10-15T00:00:00+08:00').timestamp()*1000),
    adjunctUrl='finalpage/2026-10-15/1225000001.PDF',pageColumn='SHZB')
HK_ROW = dict(NEWS_ID='fixture002', STOCK_CODE='00700<br/>80700', TITLE='測試公告，非真實披露',
    DATE_TIME='15/10/2026 12:03', FILE_TYPE='PDF', FILE_LINK='/listedco/listconews/sehk/2026/1015/2026101500001_c.pdf')
PBC_URL = 'https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/2026101519495213912/index.html'
MOF_URL = 'https://zwgls.mof.gov.cn/ywgg/202610/t20261015_4003848.htm'


def params(channel='cninfo', **changes):
    return dict(channel=channel,code='600519.SH' if channel=='cninfo' else '0700.HK',
                start_date='2026-10-01',end_date='2026-10-17',page='1',keyword='',**changes)


def cn_raw(rows=None, total=None):
    rows = [copy.deepcopy(CN_ROW)] if rows is None else rows
    return json.dumps(dict(totalAnnouncement=len(rows) if total is None else total,totalpages=1 if rows else 0,
                           hasMore=False,announcements=rows)).encode()


def hk_raw(rows=None):
    rows=[copy.deepcopy(HK_ROW)] if rows is None else rows
    return json.dumps(dict(result=json.dumps(rows),recordCnt=len(rows),hasNextRow='false')).encode()


class IndependentOfficialSourcesTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='independent-source-',dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external network'))
        guard.start();self.addCleanup(guard.stop)
        self.agent=ResearchAgent(ResearchArchive(temporary.name),ROOT,offline=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task=self.agent.create('核对贵州茅台600519.SH及腾讯0700.HK公告', objects=['600519.SH','0700.HK'],cutoff=CUTOFF)
        self.collector=EvidenceCollector(offline=False)

    def catalogue(self, channel='cninfo', raw=None):
        with patch('researchhelper.source_transport.official_announcement', side_effect=[
                CN_ISSUER if channel=='cninfo' else HK_ISSUER,
                (cn_raw() if channel=='cninfo' else hk_raw()) if raw is None else raw]) as transport:
            record=self.collector.external(self.task,'official','release_catalog',params(channel),{})
        self.task['source_records'].append(record)
        return record,transport

    def test_new_tasks_and_legacy_gates(self):
        self.assertEqual(self.task['official_source_expansion_version'],1)
        self.task.pop('official_source_expansion_version')
        before=copy.deepcopy(self.task['ledger'])
        for parameter in (params(),dict(channel='pbc_news',page='0'),dict(channel='mof_news',page='0')):
            with self.assertRaises(ContractError): self.collector.external(self.task,'official','release_catalog',parameter,{})
        self.assertEqual(before,self.task['ledger'])
        channels=acquisition_channels(self.task)['channels']
        self.assertNotIn('cninfo',next(c for c in channels if c['id']=='official')['registered_channels'])

    def test_mainland_issuer_key_is_actual_not_synthesized(self):
        issuer=issuer_from_response(CN_ISSUER,'cninfo','600519.SH')
        self.assertEqual(issuer['issuer_id'],'gssh0600519')
        spec=request_spec(params(),'catalogue',issuer)
        self.assertIn(b'stock=600519%2Cgssh0600519',spec['body'])
        bad=json.loads(CN_ISSUER);bad[0]['code']='000001'
        with self.assertRaises(ContractError):issuer_from_response(json.dumps(bad).encode(),'cninfo','600519.SH')

    def test_mainland_platform_type_is_not_interpreted_as_exchange(self):
        raw=json.dumps([dict(code='000001',orgId='gssz0000001',type='shj',category='A股',zwjc='平安银行')]).encode()
        issuer=issuer_from_response(raw,'cninfo','000001.SZ')
        p=params();p['code']='000001.SZ'
        self.assertEqual(issuer['publisher_type'],'shj')
        self.assertEqual(issuer['exchange'],'SZ')
        self.assertIn(b'column=szse',request_spec(p,'catalogue',issuer)['body'])

    def test_wrong_market_and_bad_row_do_not_replace_usable_rows(self):
        wrong=dict(CN_ROW,pageColumn='SZZB')
        bad=dict(CN_ROW,announcementTime=None)
        result=parse_announcements(cn_raw([CN_ROW,wrong,bad]),CN_ISSUER,params(),CUTOFF)
        self.assertEqual(len(result['entries']),1)
        self.assertEqual(result['coverage']['excluded']['subject'],1)
        self.assertEqual(result['coverage']['excluded']['invalid_row'],1)

    def test_hong_kong_code_alias_is_observed_in_master(self):
        issuer=issuer_from_response(HK_ISSUER,'hkex','0700.HK')
        self.assertEqual(issuer['code'],'00700')
        self.assertEqual(issuer['issuer_id'],7609)
        spec=request_spec(params('hkex'),'catalogue',issuer)
        self.assertIn('stockId=7609',spec['url'])
        self.assertNotIn('stockId=700&',spec['url'])
        self.assertIn('fromDate=20261001',spec['url'])
        self.assertIn('toDate=20261017',spec['url'])

    def test_invalid_or_ambiguous_mapping_is_not_guessed(self):
        for payload in (b'[]',b'<html>captcha</html>',json.dumps([*json.loads(HK_ISSUER),json.loads(HK_ISSUER)[0]]).encode()):
            with self.assertRaises(ContractError):issuer_from_response(payload,'hkex','0700.HK')

    def test_subject_and_dates_are_filtered_from_real_rows(self):
        wrong=copy.deepcopy(CN_ROW);wrong['orgId']='foreign'
        future=copy.deepcopy(CN_ROW);future['announcementTime']=int(datetime.fromisoformat('2026-11-01T00:00:00+08:00').timestamp()*1000)
        result=parse_announcements(cn_raw([CN_ROW,wrong,future]),CN_ISSUER,params(),CUTOFF)
        self.assertEqual(len(result['entries']),1)
        self.assertEqual(result['coverage']['excluded']['subject'],1)
        self.assertEqual(result['coverage']['excluded']['date'],1)
        self.assertIsNone(result['entries'][0]['published_at'])
        self.assertIsNone(result['entries'][0]['listed_at'])

    def test_hk_time_is_listing_time_not_verified_first_publication(self):
        result=parse_announcements(hk_raw(),HK_ISSUER,params('hkex'),CUTOFF)
        row=result['entries'][0]
        self.assertEqual(row['listed_at'],'2026-10-15T12:03:00+08:00')
        self.assertIsNone(row['published_at'])
        p=params('hkex');p['end_date']='2026-10-15'
        early=parse_announcements(hk_raw(),HK_ISSUER,p,'2026-10-15T11:59:00+08:00')
        self.assertEqual(early['entries'],[])

    def test_empty_and_broken_results_are_distinct(self):
        self.assertEqual(parse_announcements(cn_raw([]),CN_ISSUER,params(),CUTOFF)['entries'],[])
        self.assertEqual(parse_announcements(hk_raw([]),HK_ISSUER,params('hkex'),CUTOFF)['entries'],[])
        for raw in (b'{}', b'<html>blocked</html>', cn_raw([],total=3)):
            with self.assertRaises(ContractError):parse_announcements(raw,CN_ISSUER,params(),CUTOFF)

    def test_keyword_is_title_only_not_a_body_search(self):
        p=params();p['keyword']='不匹配'
        result=parse_announcements(cn_raw(),CN_ISSUER,p,CUTOFF)
        self.assertEqual(result['entries'],[])
        self.assertEqual(result['coverage']['keyword_scope'],'标题，不检索正文')

    def test_hk_page_reads_its_prefix_slice_only(self):
        rows=[]
        for index in range(101):
            row=dict(HK_ROW,NEWS_ID=str(index),FILE_LINK=f'/listedco/listconews/sehk/2026/1015/20261015{index:05d}_c.pdf');rows.append(row)
        p=params('hkex');p['page']='2'
        result=parse_announcements(hk_raw(rows),HK_ISSUER,p,CUTOFF)
        self.assertEqual(len(result['entries']),1)
        self.assertEqual(result['entries'][0]['provider_id'],'100')
        p['page']='11'
        with self.assertRaises(ContractError):validate_parameters(p,CUTOFF)

    def test_request_scope_and_budget_preflight(self):
        p=params();p['code']='000001.SZ'
        with self.assertRaises(ContractError):self.collector.external(self.task,'official','release_catalog',p,{})
        self.assertEqual(self.task['ledger']['used']['data_requests'],0)
        for field,value in (('end_date','2026-12-01'),('start_date','2026-11-01'),('keyword','x\n'),('page','0')):
            p=params();p[field]=value
            with self.assertRaises(ContractError):validate_parameters(p,CUTOFF)

    def test_two_catalogue_sends_are_metered_separately(self):
        record,transport=self.catalogue()
        self.assertEqual(transport.call_count,2)
        self.assertEqual(self.task['ledger']['used']['data_requests'],2)
        self.assertEqual(self.task['ledger']['used']['model_calls'],0)
        self.assertEqual(self.task['ledger']['dispatches'][0]['role'],'issuer_lookup')
        self.assertEqual(len(release_choices(self.task)),1)
        self.assertIsNone(record['snapshot']['entries'][0]['published_at'])

    def test_one_remaining_request_cannot_silently_send_two(self):
        self.task['ledger']['limits']['data_requests']=1
        with patch('researchhelper.source_transport.official_announcement',return_value=CN_ISSUER) as transport:
            with self.assertRaises(ContractError):self.collector.external(self.task,'official','release_catalog',params(),{})
        self.assertEqual(transport.call_count,1)
        self.assertEqual(self.task['ledger']['used']['data_requests'],1)

    def test_existing_issuer_mapping_is_reused_for_another_date_query(self):
        self.catalogue()
        p=params();p['start_date']='2026-10-10'
        with patch('researchhelper.source_transport.official_announcement',return_value=cn_raw()) as transport:
            record=self.collector.external(self.task,'official','release_catalog',p,{})
        self.assertEqual(transport.call_count,1)
        self.assertEqual(record['snapshot']['issuer_original_base64'],base64.b64encode(CN_ISSUER).decode())
        self.assertEqual(self.task['ledger']['used']['data_requests'],3)

    def test_tampered_directory_or_lookup_cannot_select_original(self):
        record,_=self.catalogue()
        self.assertEqual(len(selection_references(self.task)),1)
        record['snapshot']['entries'][0]['title']='forged'
        record['hash']=fingerprint(record['snapshot'])
        self.assertEqual(release_choices(self.task),[])
        record['snapshot']['issuer_original_base64']=base64.b64encode(b'[]').decode()
        record['hash']=fingerprint(record['snapshot'])
        self.assertEqual(release_choices(self.task),[])

    def test_cross_task_original_selection_is_rejected(self):
        record,_=self.catalogue();choice=release_choices(self.task)[0]
        foreign=copy.deepcopy(self.task);foreign['id']='foreign'
        with self.assertRaises(ContractError):validate_request(foreign,'release_original',{key:choice[key] for key in ('catalog_id','release_id')})

    def test_raw_catalogues_are_not_body_or_model_base64(self):
        self.catalogue()
        self.assertEqual(extractable_source_views(self.task),[])
        view=source_views(self.task)[0]['raw_response']
        self.assertNotIn('original_base64',view)
        self.assertNotIn('issuer_original_base64',view)
        self.assertEqual(len(view['entries']),1)

    def test_pdf_original_address_cannot_escape_official_source(self):
        valid='https://static.cninfo.com.cn/finalpage/2026-10-15/1225000001.PDF'
        self.assertEqual(original_url(valid,'cninfo'),valid)
        for bad in (valid+'?next=x',valid+'#x',valid.replace('https:','http:'),valid.replace('cninfo.com.cn','cninfo.com.cn.evil.invalid'),valid.replace('.cn/','.cn:444/')):
            with self.assertRaises(ContractError):original_url(bad,'cninfo')

    def test_selected_pdf_body_uses_material_provenance(self):
        from pypdf import PdfWriter
        import io
        writer=PdfWriter();writer.add_blank_page(100,100);output=io.BytesIO();writer.write(output)
        self.catalogue();choice=selection_references(self.task)[0]
        p=canonical_request_parameters(self.task,'release_original',dict(selection_ref=choice['selection_ref']))
        with patch('researchhelper.source_transport.official_announcement',return_value=output.getvalue()):
            record=self.collector.external(self.task,'official','release_original',p,{})
        self.task['source_records'].append(record)
        validate_original(self.task,record)
        self.assertEqual(record['snapshot']['status'],'解析受限')
        self.assertEqual(record['snapshot']['parts'][0]['locator'],'第1页')
        self.assertTrue(record['snapshot']['name'].endswith('.pdf'))
        self.assertFalse(record.get('current_original_receipt'))

    def test_domestic_policy_links_and_https_normalization(self):
        for channel,url in (('pbc_news',PBC_URL),('mof_news',MOF_URL)):
            raw=('<a href="'+url.replace('https:', 'http:')+'">测试政策</a>').encode() if channel=='mof_news' else ('<a href="'+url+'">测试政策</a>').encode()
            result=parse_catalogue(raw,CUTOFF,channel=channel)
            self.assertEqual(result['entries'][0]['url'],url)
            self.assertIsNone(result['entries'][0]['published_at'])
            self.assertEqual(article_url(url,channel=channel),url)
            for bad in (url+'?x=1',url.replace('gov.cn','gov.cn.evil.invalid')):
                with self.assertRaises(ContractError):article_url(bad,channel=channel)

    def test_policy_html_is_downloaded_only_from_retained_link(self):
        raw=('<a href="'+PBC_URL+'">测试政策</a>').encode()
        with patch('researchhelper.source_transport.official_html',return_value=raw):
            record=self.collector.external(self.task,'official','release_catalog',dict(channel='pbc_news',page='0'),{})
        self.task['source_records'].append(record)
        choice=release_choices(self.task)[0]
        with patch('researchhelper.source_transport.official_html',return_value=b'<head><title>Fixture</title></head><table><tr><td><table><tr><td>Navigation</td></tr></table><div id="zoom"><p>Policy fixture only.</p></div></td></tr></table>'):
            record=self.collector.external(self.task,'official','release_original',{key:choice[key] for key in ('catalog_id','release_id')},{})
        self.task['source_records'].append(record);validate_original(self.task,record)
        self.assertEqual(record['snapshot']['status'],'已解析')
        self.assertEqual(record['snapshot']['extract_version'],'html-pbc-1')
        self.assertNotIn('Navigation',' '.join(row['text'] for row in record['snapshot']['parts']))

    def test_planner_and_runtime_share_correlated_channel_shapes(self):
        schemas=_planned_request_schemas(self.task)
        for p in (params(),params('hkex'),dict(channel='pbc_news',page=0),dict(channel='mof_news',page='0')):
            request=dict(id='official_test',source='official',operation='release_catalog',parameters=p,question_ids=['q1'],selection_reason='核对相关原文')
            self.assertTrue(any(_matches(request,schema) for schema in schemas))
            self.assertTrue(canonical_request_parameters(self.task,'release_catalog',p))
        request['parameters']=dict(channel='mof_news',page='1')
        self.assertFalse(any(_matches(request,schema) for schema in schemas))

    def test_json_response_checkpoint_uses_api_storage_not_material_suffix(self):
        self.collector.original_keeper = self.agent.archive.retain_original
        self.collector.journal = self.agent.archive.save
        record, _ = self.catalogue()
        self.agent.archive.save(self.task)
        saved = self.agent.archive.get(self.task['id'])
        self.assertEqual(len(saved['official_response_checkpoints']), 2)
        raw = base64.b64decode(saved['official_response_checkpoints'][0]['original_base64'])
        self.assertEqual(raw, CN_ISSUER)
        self.assertEqual(record['snapshot']['issuer_original_base64'], base64.b64encode(raw).decode())
        self.assertEqual(self.agent.archive.retained_originals(self.task), [])

    def test_failed_catalogue_keeps_real_response_bytes(self):
        self.collector.journal = self.agent.archive.save
        with patch('researchhelper.source_transport.official_announcement', side_effect=[CN_ISSUER, b'{}']):
            with self.assertRaises(ContractError):
                self.collector.external(self.task,'official','release_catalog',params(),{})
        saved = self.agent.archive.get(self.task['id'])
        self.assertEqual(base64.b64decode(saved['official_response_checkpoints'][-1]['original_base64']), b'{}')
        self.assertEqual(saved['official_response_checkpoints'][-1]['role'], 'catalogue')
        self.assertEqual(saved['ledger']['used']['data_requests'], 2)
        self.assertFalse(saved['source_records'])

    def test_issuer_checkpoint_is_reused_even_when_first_query_is_empty(self):
        self.catalogue(raw=cn_raw([]))
        p=params();p['start_date']='2026-10-10'
        with patch('researchhelper.source_transport.official_announcement',return_value=cn_raw()) as transport:
            self.collector.external(self.task,'official','release_catalog',p,{})
        self.assertEqual(transport.call_count,1)

    def test_policy_scoped_parser_ignores_nested_layout_tables_and_scripts(self):
        from researchhelper.evidence import extract_material
        raw = (b'<head><title>Policy</title><meta name="PubDate" content="2026-10-15"></head>'
            b'<table><tr><td><table><tr><td>Navigation</td></tr></table>'
            b'<div id="zoom"><p>Actual policy &amp; text.</p><script>ignore this</script></div></td></tr></table>')
        material = extract_material('pbc.html',raw,extract_version='html-pbc-1')
        text=' '.join(row['text'] for row in material['parts'])
        self.assertEqual(material['status'],'已解析')
        self.assertIn('Actual policy & text.',text)
        self.assertIn('PubDate',text)
        self.assertNotIn('Navigation',text)
        self.assertNotIn('ignore this',text)
        for invalid in (b'<p>No registered body</p>',b'<div id="zoom">A</div><div id="zoom">B</div>'):
            with self.assertRaises(ContractError): extract_material('pbc.html',invalid,extract_version='html-pbc-1')

    def test_explicit_update_adopts_channels_without_rewriting_old_report(self):
        from researchhelper.planning import input_fingerprint
        agent=ResearchAgent(self.agent.archive,ROOT,offline=True,draft_first=True)
        self.addCleanup(agent.close_runtime)
        task=agent.create('版本迁移合成测试',cutoff=CUTOFF)
        for key in ('official_source_expansion_version','official_policy_channels_version'):
            task.pop(key,None)
        task['official_response_checkpoints']=[dict(id='synthetic-prior-response')]
        task['input_hash']=input_fingerprint(task)
        agent.archive.save(task)
        task=agent.start(task['id'],dict(data_requests=[],draft_report=dict(
            title='迁移合成测试',conclusion='仅验证版本迁移，不代表真实研究。',logics=[],risks=[])))
        self.assertTrue(task['delivery'])
        old=copy.deepcopy(agent.archive.report(task['id'],task['version']))
        self.assertNotIn('official_source_expansion_version',agent.archive.get(task['id']))
        updated=agent.prepare_update(task['id'],'2026-10-18T18:00:00+08:00')
        self.assertEqual(updated['official_source_expansion_version'],1)
        self.assertEqual(updated['official_policy_channels_version'],1)
        self.assertNotIn('official_response_checkpoints',updated)
        self.assertNotIn('official_source_expansion_version',agent.archive.get(task['id'],task['version']))
        self.assertEqual(agent.archive.report(task['id'],task['version']),old)
        self.assertEqual(updated['ledger']['used']['model_calls'],0)

    def test_source_map_has_five_actual_families_without_permission_claims(self):
        catalogue=source_catalogue(self.task)
        self.assertEqual(len(catalogue['families']),5)
        self.assertEqual(catalogue['default_source'],'ifind')
        channels=[route.get('channel') for family in catalogue['families'] for route in family['routes']]
        self.assertTrue({'cninfo','hkex','pbc_news','mof_news'} <= set(channels))
        self.assertTrue(all(route['connection_status']=='按实际响应核验' for family in catalogue['families'] for route in family['routes']))
        self.assertFalse(catalogue['policy']['ordinary_numbers_download_pdf'])
        for family in catalogue['families']:
            for route in family['routes']:
                for operation in route['operations']:
                    # Internal filing is selected dynamically after a directory.
                    self.assertIn(operation,{row['operation'] for row in __import__('researchhelper.evidence',fromlist=['active_source_capabilities']).active_source_capabilities()[route['source']]['registered_operations']})


if __name__ == '__main__':
    unittest.main()
