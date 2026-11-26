"""Sparse financial readouts retain both original observations and the calculation."""
from copy import deepcopy
import socket,unittest
from unittest.mock import patch
from lxml import html
from tests import ROOT
from tests.test_draft_financial_period import sample,rehash
from tests.test_financial_visual_cohorts import freeze_report
from researchhelper.contracts import canonical,fingerprint
from researchhelper.draft_analysis import analyze
from researchhelper.draft_visuals import logic_figure_plan
from researchhelper.report_designer import design


def fixture(field='ths_np_stock'):
    task=sample();task.update(questions=[],evidence=[],calculations=[],knowledge={},gaps=[],tracking=[],theses=[])
    for index,source in enumerate(task['source_records']):
        period=source['parameters']['indipara'][0]['indiparams'][0]
        source['parameters']['indipara']=[dict(indicator=field,indiparams=[period,'1'])]
        source['snapshot']['tables'][0]['table']={field:[10 if index==0 else 12]};rehash(source)
    return freeze_report(task)


class SingleFinancialChangeVisualTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'))
        guard.start();self.addCleanup(guard.stop)

    def test_unknown_unit_uses_the_existing_verified_percentage_and_its_inputs(self):
        _,content=fixture()
        before=canonical(content)
        self.assertEqual(logic_figure_plan(content),[])
        logic=html.document_fromstring(design(content,ROOT)['html']).get_element_by_id('logic-1')
        result, = logic.xpath('.//p[@class="measured-reading"]//span[@data-calculation-ref]')
        self.assertIn('20.00%',result.text_content())
        self.assertEqual(result.get('data-calculation-inputs').split(),[row['id'] for row in content['draft']['facts']])
        self.assertEqual(canonical(content),before)

    def test_both_profiles_keep_two_readings_and_full_raw_dataset(self):
        _,content=fixture()
        for profile in ('standard','detailed'):
            with self.subTest(profile=profile):
                output=design(content,ROOT,profile=profile);tree=html.document_fromstring(output['html'])
                figures=[block['spec'] for block in output['document']['blocks'] if block['kind']=='chart']
                self.assertEqual(figures,[])
                facts = content['draft']['facts']
                original_ids = {row['id'] for row in facts}
                logic = tree.get_element_by_id('logic-1')
                readings = logic.xpath('.//p[@class="measured-reading"]/*[@data-observation-id]')
                risk = tree.get_element_by_id('risk').text_content()
                if profile == 'standard':
                    self.assertFalse(readings, 'Unconfirmed amounts belong in the linked original data')
                    result, = logic.xpath('.//p[@class="measured-reading"]//*[@data-calculation-ref]')
                    self.assertEqual(set(result.get('data-calculation-inputs').split()), original_ids)
                    link, = result.xpath('./a')
                    record = tree.get_element_by_id(link.get('href').removeprefix('#'))
                    self.assertEqual(set(record.xpath('.//tr/@data-observation-id')), original_ids)
                    for fact in facts:
                        row, = record.xpath('.//tr[@data-observation-id=$ref]', ref=fact['id'])
                        self.assertEqual(row.xpath('./td[last()-1]/text()'), [str(fact['value'])])
                    self.assertNotIn('计量单位尚未核实',tree.find('.//main').text_content())
                    self.assertIn('若报表披露口径不同，同比和比率需重新计算。',risk)
                else:
                    self.assertEqual({node.get('data-observation-id') for node in readings}, original_ids)
                    for node in readings:
                        self.assertIn('原值为', node.text_content())
                        self.assertIn('（单位待核对）', node.text_content())
                    self.assertIn('相关金额的计量单位尚未核实，跨期比较以单位一致为前提。',risk)
                self.assertEqual([row[3] for row in output['document']['dataset']['rows']],[10,12])
                self.assertEqual([row[4] for row in output['document']['dataset']['rows']],['原始单位待核对']*2)

    def test_known_units_do_not_create_a_two_reading_figure(self):
        task,content=fixture()
        for fact in content['draft']['facts']:fact['unit']='元'
        content['draft']['calculations']=analyze(task,content['draft']['facts'])['results']
        figures=logic_figure_plan(content)
        self.assertEqual(figures,[])

    def test_missing_or_invalid_calculations_do_not_create_a_percentage(self):
        for mode in ('missing','result','inputs','condition','method','period','basis'):
            with self.subTest(mode=mode):
                _,content=fixture();calc=content['draft']['calculations'][0]
                if mode=='missing':content['draft']['calculations']=[]
                elif mode=='result':calc.update(value=999,display='999.00')
                elif mode=='inputs':calc['inputs']['a']=1
                elif mode=='condition':calc['conditional_equal_scale']=False
                elif mode=='method':calc['method_version']=999
                elif mode=='period':content['draft']['facts'][1]['period']='20250630'
                else:content['draft']['facts'][1]['series_basis']='different-statement'
                self.assertFalse(any(figure.get('kind')=='changes' for figure in logic_figure_plan(content)))

    def test_nonpositive_base_does_not_invent_growth(self):
        for base in (0,-10):
            with self.subTest(base=base):
                task,content=fixture();content['draft']['facts'][0].update(value=base,raw_value=base)
                content['draft']['calculations']=analyze(task,content['draft']['facts'])['results']
                self.assertFalse(any(figure.get('kind')=='changes' for figure in logic_figure_plan(content)))

    def test_foreign_sources_do_not_supply_the_single_change_chart(self):
        _,content=fixture()
        for source in content['source_records']:source['task_id']='foreign'
        self.assertFalse(any(figure.get('kind')=='changes' for figure in logic_figure_plan(content)))

    def test_balance_change_is_named_as_a_balance_without_cash_attribution(self):
        _,content=fixture('ths_inventory_stock')
        tree=html.document_fromstring(design(content,ROOT)['html'])
        paragraph, = tree.get_element_by_id('logic-1').xpath('.//p[@class="measured-reading"]')
        result, = paragraph.xpath('.//span[@data-calculation-ref]')
        link, = result.xpath('./a')
        self.assertIn('存货余额',paragraph.text_content())
        self.assertIn('存货余额',link.get('aria-label'))
        self.assertNotRegex(paragraph.text_content(), r'释放|现金流')
        self.assertEqual(result.text_content(),'20.00%')

    def test_incomparable_or_single_period_data_still_saves_a_readable_presentation(self):
        _,content=fixture();content['draft']['calculations']=[];content['draft']['facts']=content['draft']['facts'][:1]
        content['draft']['logics'][0]['data_refs']=[content['draft']['facts'][0]['id']]
        content['draft']['logics'][0]['calculation_refs']=[]
        content['content_hash']=fingerprint({key:value for key,value in content.items() if key!='content_hash'})
        result=design(content,ROOT);self.assertIn('分别核对同口径',result['html'])
        self.assertEqual(len(result['document']['dataset']['rows']),1)
        self.assertFalse(any(block['kind']=='chart' for block in result['document']['blocks']))


if __name__=='__main__':unittest.main()
