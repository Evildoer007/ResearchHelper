"""Explicit quote currency survives reports and exports without changing raw quantities."""
from copy import deepcopy
from pathlib import Path
import csv,io,json,socket,tempfile,unittest
from unittest.mock import patch
from lxml import html
from core.session import Session
from tests import ROOT
from tests.accept_active_update_package import INPUTS,PROPOSAL
from researchhelper.contracts import fingerprint
from researchhelper.draft_sources import observations,writing_context
from researchhelper.planning import input_fingerprint
from researchhelper.publication_content import observation_unit,publication_dataset
from researchhelper.draft_visuals import logic_figure_plan


class CurrencyPresentationTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix='rh-currency-');self.addCleanup(self.folder.cleanup)
        self.session=Session(self.folder.name);self.addCleanup(self.session.agent.close_runtime)
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('Offline only'));guard.start();self.addCleanup(guard.stop)
    def task(self,version=13,records=None):
        task=self.session.create('核对002594.SZ两期经营数据与0700.HK两日人民币行情。',entry='混合',objects=['002594.SZ','0700.HK'],research_mode='current')
        task['draft_source_reader_version']=version;task['input_hash']=input_fingerprint(task);task['synthetic']=True
        selected=[row for row in INPUTS if row['operation'] in ('basic_data_service','cmd_history_quotation')] if records is None else records
        task['source_records']=[]
        for index,row in enumerate(selected):
            params=deepcopy(row['parameters']);snapshot=deepcopy(row['snapshot'])
            task['source_records'].append(dict(id='retained-'+str(index),kind='api',source='ifind',content_version=1,
                task_id=task['id'],research_version=task['version'],operation=row['operation'],parameters=params,
                snapshot=snapshot,hash=fingerprint(snapshot),request_hash=fingerprint(dict(source='ifind',operation=row['operation'],parameters=params)),status='成功待核验'))
        return self.session.archive.save(task)
    def test_currency_is_request_metadata_and_never_a_value_conversion(self):
        current=self.task();legacy=deepcopy(current);legacy['draft_source_reader_version']=12
        before=deepcopy(current['source_records']);old=observations(legacy);new=observations(current)
        self.assertEqual(len(new),16)
        self.assertEqual([(row['raw_value'],row['unit'],row['period']) for row in old],[(row['raw_value'],row['unit'],row['period']) for row in new])
        quotes=[row for row in new if row['source']=='ifind' and row['field'] in ('open','high','low','close')]
        self.assertEqual(len(quotes),8);self.assertTrue(all(row['currency']=='RMB' and row['unit']=='元' and observation_unit(row)=='人民币元' for row in quotes))
        self.assertEqual(current['source_records'],before)
    def test_legacy_rows_do_not_gain_currency_or_change_their_writer_context(self):
        task=self.task(12);facts=observations(task)
        self.assertFalse(any('currency' in row for row in facts))
        self.assertFalse(any('currency' in row for row in writing_context(task,facts)['observations']))
        self.assertEqual({observation_unit(row) for row in facts if row['field']=='close'},{'元'})
    def test_writer_gets_currency_only_for_the_registered_price_series(self):
        task=self.task();context=writing_context(task,observations(task))
        prices=[row for row in context['observations'] if row['field'] in ('open','high','low','close')]
        self.assertTrue(prices);self.assertTrue(all(row['currency']=='RMB' and row['unit']=='元' for row in prices))
        self.assertFalse(any('currency' in row for row in context['observations'] if row['field']=='ths_revenue_stock'))
    def test_a_code_or_a_loose_currency_tag_cannot_supply_missing_units(self):
        task=self.task();quote=next(row for row in observations(task) if row['field']=='close')
        for change in (dict(unit='原始单位待核对'),dict(currency=None),dict(source='official'),dict(unit_reference=None),dict(unit_reference=['not a reference']),dict(raw_unit='')):
            with self.subTest(change=change):self.assertNotEqual(observation_unit(dict(quote,**change)),'人民币元')
        forged=deepcopy(quote);forged['unit_reference']['currency_parameter']='HKD';self.assertEqual(observation_unit(forged),'元')
    def test_index_points_do_not_become_currency(self):
        item=deepcopy(next(row for row in INPUTS if row['operation']=='cmd_history_quotation'))
        item['parameters']['codes']='000300.SH'
        for row in item['snapshot']['tables']:row['thscode']='000300.SH'
        task=self.task(records=[item]);task['question']='核对000300.SH指数点位';task['objects']=['000300.SH'];task['input_hash']=input_fingerprint(task)
        facts=observations(task);self.assertEqual(len(facts),8)
        self.assertTrue(all(row['unit']=='点' and 'currency' not in row and observation_unit(row)=='点' for row in facts))
    def test_report_tables_charts_and_dataset_use_the_same_explicit_unit(self):
        task=self.task();done=self.session.draft(task['id'],report=PROPOSAL)
        self.assertTrue(done.get('delivery'));content=self.session.archive.report(task['id'],1)['content']
        price_ids={row['id'] for row in content['draft']['facts'] if row.get('currency')=='RMB'}
        specs=[row for row in logic_figure_plan(content) if row.get('data_refs') and set(row['data_refs'])<=price_ids]
        self.assertTrue(specs);self.assertTrue(all(row['unit']=='人民币元' and row['auto_visual']['data']['unit']=='人民币元' for row in specs))
        dataset=publication_dataset(content);self.assertEqual(sum(row[4]=='人民币元' for row in dataset['rows']),8)
        for profile in ('standard','detailed'):
            tree=html.document_fromstring(self.session.export(task['id'],profile=profile).read_text())
            document=json.loads(tree.get_element_by_id('rh-publication-document').text)
            self.assertEqual(document['dataset'],dataset)
            if profile=='detailed':self.assertIn('人民币元',tree.xpath('//main')[0].text_content())
            else:self.assertTrue(any(block.get('spec',{}).get('unit')=='人民币元' for block in document['blocks'] if block['kind']=='chart'))
    def test_detailed_panels_keep_complete_value_tables_in_readable_groups(self):
        task=self.task();done=self.session.draft(task['id'],report=PROPOSAL)
        before=deepcopy(self.session.archive.report(task['id'],1)['content'])
        standard=self.session.export(task['id'],profile='standard').read_bytes()
        tree=html.document_fromstring(self.session.export(task['id'],profile='detailed').read_text())
        groups=tree.xpath('//main//*[contains(concat(" ",normalize-space(@class)," ")," paper-figure-group ")]')
        self.assertTrue(groups)
        for group in groups:
            if group.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," evidence-ledger ")]'):
                self.assertLessEqual(len(group.xpath('./figure')),2)
        ledger_values=[node.text_content() for node in tree.xpath('//main//figure//table//td')]
        self.assertIn('371,280,948,000',ledger_values)
        self.assertIn('344,815,421,000',ledger_values)
        self.assertEqual(len(tree.xpath('//main//figure')),6)
        self.assertEqual(standard,self.session.export(task['id'],profile='standard').read_bytes())
        self.assertEqual(before,self.session.archive.report(task['id'],1)['content'])
        self.assertEqual(done['ledger']['used']['model_calls'],0)
        self.assertEqual(done['ledger']['used']['data_requests'],0)

    def test_currency_export_preserves_raw_numbers_and_does_not_spend_budget(self):
        task=self.task();done=self.session.draft(task['id'],report=PROPOSAL);before=deepcopy(done['ledger'])
        expected=publication_dataset(self.session.archive.report(task['id'],1)['content'])['rows']
        for profile in ('standard','detailed'):
            path=self.session.export(task['id'],profile=profile,format='csv');rows=list(csv.reader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
            self.assertEqual(rows[1:],[[str(value) if value is not None else '' for value in row] for row in expected])
        self.assertEqual(self.session.status(task['id'])['ledger'],before)
    def test_old_completed_prose_can_still_recover_without_new_currency_fields(self):
        task=self.task(12)
        with patch('researchhelper.draft_workflow.normalize_report',side_effect=OSError('Controlled local failure')):failed=self.session.draft(task['id'],report=PROPOSAL)
        self.assertEqual(failed['status'],'失败');self.session.agent.close_runtime()
        other=Session(self.folder.name);self.addCleanup(other.agent.close_runtime);done=other.resume(task['id'])
        self.assertTrue(done.get('delivery'));self.assertEqual(done['draft_source_reader_version'],12)
        self.assertFalse(any('currency' in row for row in done['draft_report']['facts']))
        self.assertEqual(done['ledger']['used']['model_calls'],0);self.assertEqual(done['ledger']['used']['data_requests'],0)


if __name__=='__main__':unittest.main()
