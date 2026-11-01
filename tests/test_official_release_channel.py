"""New fixed-channel guards; fixture inputs are not autonomous research QA."""
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector, candidate_evidence, data_request_contract, acquisition_channels
from researchhelper.official_sources import (article_url, catalogue_url, parse_catalogue,
    release_choices, validate_request, validate_original, download_html, MAX_BYTES)
from researchhelper.reasoning import source_views, extractable_source_views, evidence_contract
from researchhelper.output_schema import _planned_request_schemas, _matches

URL='https://www.stats.gov.cn/sj/zxfbhjd/202609/t20260930_1965449.html'
CATALOG=('<a href="'+URL+'">2026年9月中国采购经理指数运行情况</a>'
    '<a href="'+URL+'">重复链接</a>'
    '<a href="https://example.invalid/a">外部</a>').encode()
ORIGINAL=('<html><head><meta name="PubDate" content="2026/09/30 09:30"></head>'
    '<body><h1>2026年9月中国采购经理指数运行情况</h1>'
    '<p>原件测试正文，非真实宏观数字。</p><script>伪造正文</script></body></html>').encode()


class OfficialReleaseChannelTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='official-channel-',dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        self.agent=ResearchAgent(ResearchArchive(temporary.name),ROOT,offline=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task=self.agent.create('核对中国采购经理指数发布原文',intent='原件内容核对',
            objects=['中国采购经理指数'],cutoff='2026-10-16T18:00:00+08:00')
        self.collector=EvidenceCollector(offline=False)
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external network'))
        guard.start();self.addCleanup(guard.stop)

    def catalogue(self):
        with patch('researchhelper.source_transport.official_html',return_value=CATALOG):
            record=self.collector.external(self.task,'official','release_catalog',
                dict(channel='nbs_release',page='0'),{})
        self.task['source_records'].append(record)
        return record

    def original(self):
        self.catalogue()
        choice=release_choices(self.task)[0]
        parameters={key:choice[key] for key in ('catalog_id','release_id')}
        with patch('researchhelper.source_transport.official_html',return_value=ORIGINAL):
            record=self.collector.external(self.task,'official','release_original',parameters,{})
        self.task['source_records'].append(record)
        return record

    def test_only_exact_registered_public_urls(self):
        self.assertEqual(article_url(URL),URL)
        for bad in (URL+'?token=x',URL+'#x',URL.replace('https','http'),
                    URL.replace('www.stats.gov.cn','www.stats.gov.cn.evil.invalid'),
                    URL.replace('/sj/','/private/'),'https://127.0.0.1/a',
                    'https://user:pass@www.stats.gov.cn/sj/zxfb/202609/t20260930_1.html'):
            with self.subTest(url=bad),self.assertRaises(ContractError):article_url(bad)

    def test_directory_has_finite_pages_and_no_arbitrary_path(self):
        self.assertEqual(catalogue_url('0'),'https://www.stats.gov.cn/sj/zxfbhjd/')
        for bad in ('4','../0','0?next=x',0,True):
            with self.subTest(page=bad),self.assertRaises(ContractError):catalogue_url(bad)

    def test_directory_keeps_bytes_deduplicates_and_does_not_claim_completeness(self):
        result=parse_catalogue(CATALOG,self.task['cutoff'])
        self.assertEqual(len(result['entries']),1)
        self.assertEqual(result['entries'][0]['published_at'],None)
        self.assertEqual(result['coverage']['completeness'],'未证实')
        self.assertIn('original_base64',result)

    def test_future_path_hints_are_excluded_not_relabelled(self):
        payload=CATALOG.replace(b'20260930',b'20261230')
        result=parse_catalogue(payload,self.task['cutoff'])
        self.assertEqual(result['entries'],[])
        self.assertEqual(result['coverage']['skipped_future'],2)

    def test_broken_directory_is_not_a_successful_empty_result(self):
        with self.assertRaises(ContractError):parse_catalogue(b'<p>blocked</p>',self.task['cutoff'])

    def test_original_requires_retained_catalogue_before_spending(self):
        with self.assertRaises(ContractError):self.collector.external(self.task,'official','release_original',
            dict(catalog_id='foreign',release_id='foreign'),{})
        self.assertEqual(self.task['ledger']['used']['data_requests'],0)

    def test_two_mocked_sends_use_one_ledger_and_no_model(self):
        record=self.original()
        self.assertEqual(self.task['ledger']['used']['data_requests'],2)
        self.assertEqual(self.task['ledger']['used']['model_calls'],0)
        self.assertEqual(record['source'],'official')
        self.assertEqual(record['kind'],'material')
        self.assertEqual(record['snapshot']['status'],'已解析')
        self.assertEqual(record['source_check'],'待核验')
        self.assertNotIn('伪造正文',''.join(p['text'] for p in record['snapshot']['parts']))
        validate_original(self.task,record)

    def test_external_offline_gate_still_blocks_registered_channel(self):
        collector=EvidenceCollector(offline=True)
        with self.assertRaises(ContractError):collector.external(self.task,'official','release_catalog',
            dict(channel='nbs_release',page='0'),{})
        self.assertEqual(self.task['ledger']['used']['data_requests'],0)

    def test_old_tasks_are_not_silently_upgraded(self):
        self.assertEqual(acquisition_channels()['version'],2)
        self.task.pop('official_channels_version')
        contract=data_request_contract(self.task)
        self.assertFalse(any(t['source']=='official' for t in contract['tools']))
        self.assertEqual(contract['acquisition_channels']['version'],1)
        self.assertNotIn('official',[channel['id'] for channel in contract['acquisition_channels']['channels']])
        with self.assertRaises(ContractError):validate_request(self.task,'release_catalog',dict(channel='nbs_release',page='0'))

    def test_cross_task_and_changed_directory_are_rejected(self):
        record=self.catalogue();selection=release_choices(self.task)[0]
        parameters={key:selection[key] for key in ('catalog_id','release_id')}
        foreign=copy.deepcopy(self.task);foreign['id']='foreign'
        with self.assertRaises(ContractError):validate_request(foreign,'release_original',parameters)
        record['snapshot']['entries'][0]['url']=URL+'?x=1'
        with self.assertRaises(ContractError):validate_request(self.task,'release_original',parameters)

    def test_rehashing_fake_directory_rows_does_not_rewrite_original_html(self):
        record=self.catalogue();record['snapshot']['entries'][0]['title']='伪造标题'
        record['hash']=fingerprint(record['snapshot'])
        self.assertEqual(release_choices(self.task),[])

    def test_directory_cannot_be_extracted_and_bytes_never_enter_model_context(self):
        self.catalogue()
        self.assertEqual(extractable_source_views(self.task),[])
        views=source_views(self.task)
        self.assertNotIn('original_base64',views[0]['raw_response'])
        self.assertTrue(views[0]['raw_response']['entries'])

    def test_plan_schema_and_actual_validator_share_fixed_choices(self):
        schemas=_planned_request_schemas(self.task)
        request=dict(id='official_list',source='official',operation='release_catalog',
            parameters=dict(channel='nbs_release',page='0'),question_ids=['q1'],selection_reason='核对官方原文')
        self.assertTrue(any(_matches(request,schema) for schema in schemas))
        request['parameters']['page']='100'
        self.assertFalse(any(_matches(request,schema) for schema in schemas))

    def test_original_source_metadata_cannot_be_changed_independently(self):
        record=self.original();record['snapshot']['official_origin']['url']=URL+'?x=1'
        with self.assertRaises(ContractError):validate_original(self.task,record)

    def test_preflight_requires_reason_and_rejects_duplicate_original(self):
        self.catalogue();choice=release_choices(self.task)[0]
        request=dict(id='original_a',source='official',operation='release_original',question_ids=['q1'],
            parameters={key:choice[key] for key in ('catalog_id','release_id')},selection_reason='按问题核对原文')
        with self.assertRaises(ContractError):self.agent._validated_data_needs(self.task,[request,dict(request,id='original_b')])
        request.pop('selection_reason')
        with self.assertRaises(ContractError):self.agent._validated_data_needs(self.task,[request])

    def test_second_need_reuses_retained_original_instead_of_new_send(self):
        record=self.original()
        self.agent._journal=Mock();self.agent._active=Mock(return_value=self.task)
        self.agent.collector=Mock()
        request=dict(id='reuse',source='official',operation='release_original',question_ids=['q1'],
            parameters=record['parameters'],selection_reason='复用同一原文')
        self.agent._acquire_sources(self.task,[request])
        self.agent.collector.external.assert_not_called()
        self.assertIn('reuse',record['data_need_ids'])

    def test_downloaded_original_can_complete_explicit_document_review_not_investment(self):
        record=self.original()
        body=next(part for part in record['snapshot']['parts'] if '原件测试正文' in part['text'])
        self.agent.archive.save(self.task)
        spec=dict(id='official_text',artifact_id=record['id'],object='中国采购经理指数',
            record_locator=dict(part=body['locator']),text=body['text'],period='未核验',unit='不适用',
            nature='合同政策条款',question_ids=['q1'],usage_scope='document_content',published_at=None,
            publication=evidence_contract(self.task)['document_publication_template'],
            review=dict(question_ids=['q1'],allowed_natures=['合同政策条款'],
                conditions=['仅逐字核对静态原文，不形成金融判断'],reason='同一官方原文字节'))
        done=self.agent.start(self.task['id'],dict(evidence_specs=[spec]))
        self.assertEqual(done['status'],'已完成',done.get('error') or done['gaps'])
        self.assertEqual(done['ledger']['used']['model_calls'],0)
        self.assertEqual(done['calculations'],[])
        self.assertIn(body['text'],self.agent.export(done['id'])['html'])

    def test_completed_bytes_are_retained_before_post_download_cancel(self):
        self.collector.active=Mock(side_effect=[None,ContractError('cancelled')])
        self.collector.original_keeper=Mock()
        with patch('researchhelper.source_transport.official_html',return_value=CATALOG):
            with self.assertRaisesRegex(ContractError,'cancelled'):
                self.collector.external(self.task,'official','release_catalog',dict(channel='nbs_release',page='0'),{})
        self.collector.original_keeper.assert_called_once_with(self.task,'material','nbs-catalogue-page-0.html',CATALOG)
        self.assertEqual(self.task['ledger']['used']['data_requests'],1)

    def test_redirects_wrong_type_and_oversize_are_not_downloads(self):
        for status,content_type,length,error in (
            (302,'text/html','0',urllib.error.HTTPError),
            (200,'application/pdf','0',ContractError),
            (200,'text/html',str(MAX_BYTES+1),ContractError)):
            response=Mock(status=status,headers={})
            response.getheader.side_effect=lambda key,default=None: {
                'Content-Type':content_type,'Content-Length':length}.get(key,default)
            connection=Mock();connection.getresponse.return_value=response
            with self.subTest(status=status,type=content_type),patch(
                    'researchhelper.evidence._PublicFilingConnection',return_value=connection):
                with self.assertRaises(error):download_html(URL,5)
                response.read1.assert_not_called()
                connection.close.assert_called_once()

    def test_declared_short_body_is_rejected(self):
        response=Mock(status=200)
        response.getheader.side_effect=lambda key,default=None: {
            'Content-Type':'text/html','Content-Length':'10'}.get(key,default)
        response.read1.side_effect=[b'<p>',b'']
        connection=Mock();connection.getresponse.return_value=response
        with patch('researchhelper.evidence._PublicFilingConnection',return_value=connection):
            with self.assertRaisesRegex(ContractError,'未完整接收'):download_html(URL,5)

    def test_cancel_or_exhausted_request_budget_blocks_dispatch(self):
        self.collector.active=Mock(side_effect=ContractError('cancelled'))
        with patch('researchhelper.source_transport.official_html') as send:
            with self.assertRaises(ContractError):self.collector.external(self.task,'official','release_catalog',
                dict(channel='nbs_release',page='0'),{})
            send.assert_not_called()
        self.assertEqual(self.task['ledger']['used']['data_requests'],0)
        self.collector.active=None
        self.task['ledger']['used']['data_requests']=self.task['ledger']['limits']['data_requests']
        with patch('researchhelper.source_transport.official_html') as send:
            with self.assertRaises(ContractError):self.collector.external(self.task,'official','release_catalog',
                dict(channel='nbs_release',page='0'),{})
            send.assert_not_called()


if __name__=='__main__':unittest.main()
