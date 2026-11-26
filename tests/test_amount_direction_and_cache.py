"""Direction wording and cached arithmetic cannot silently change a claim."""
from copy import deepcopy
import socket,tempfile,unittest
from unittest.mock import patch
from tests import ROOT
from tests.test_draft_client_copy import fixture
from tests.test_draft_financial_period import rehash
from researchhelper.contracts import canonical
from researchhelper.draft_analysis import analyze,current_analysis
from researchhelper.draft_sources import client_writing_context
from researchhelper.publication_content import prepare_draft_client_copy
from core.session import Session


class AmountDirectionTests(unittest.TestCase):
    def setUp(self):
        self.guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'));self.guard.start();self.addCleanup(self.guard.stop)
    def revise(self,text):
        task,facts,_=fixture();proposal=dict(title='经营观察',conclusion=text,logics=[])
        before=canonical((task,facts,proposal));result,changes=prepare_draft_client_copy(task,proposal,facts)
        self.assertEqual(canonical((task,facts,proposal)),before);return result['conclusion']
    def test_a_declining_single_amount_is_not_replaced_by_a_positive_growth(self):
        for verb in ('降至','下降至','减少到','回落至','降低到','下滑至'):
            with self.subTest(verb=verb):
                text=self.revise('营业收入'+verb+'120元。')
                self.assertEqual(text,'营业收入'+verb+'120（单位未明）。');self.assertNotIn('同比',text)
    def test_rising_single_amount_uses_only_a_compatible_comparison(self):
        for verb in ('升至','上升至','增加到','回升至','提升到'):
            with self.subTest(verb=verb):self.assertEqual(self.revise('营业收入'+verb+'120元。'),'营业收入同比20.00%。')
    def test_a_rising_cash_amount_cannot_borrow_its_actual_negative_growth(self):
        for verb in ('升至','上升到','增长至'):
            with self.subTest(verb=verb):self.assertEqual(self.revise('经营现金流'+verb+'18元。'),'经营现金流'+verb+'18（单位未明）。')
    def test_common_descending_pair_forms_preserve_endpoint_order(self):
        for verb in ('下降至','减少到','回落至','降低到','降到'):
            with self.subTest(verb=verb):self.assertEqual(self.revise('营业收入由120元'+verb+'100元。'),'营业收入由120（单位未明）'+verb+'100（单位未明）。')
    def test_common_rising_pair_forms_keep_the_existing_correct_growth(self):
        for verb in ('上升至','增加到','增长至','提升到','升到'):
            with self.subTest(verb=verb):self.assertEqual(self.revise('营业收入由100元'+verb+'120元。'),'营业收入同比20.00%。')
    def test_unknown_range_language_does_not_turn_the_first_amount_into_growth(self):
        for start in ('由','从'):
            with self.subTest(start=start):
                text=self.revise('营业收入'+start+'120元变动至100元。')
                self.assertNotIn('同比',text);self.assertIn(start+'120（单位未明）变动至100元',text)
    def test_an_unresolved_second_amount_does_not_create_a_new_comparison(self):
        self.assertEqual(self.revise('营业收入从120元上升到999元。'),'营业收入从120（单位未明）上升到999元。')
    def test_neutral_pair_connectors_are_bound_to_the_actual_endpoints(self):
        for verb in ('到','至','变为'):
            with self.subTest(verb=verb):self.assertEqual(self.revise('营业收入由100元'+verb+'120元。'),'营业收入同比20.00%。')


class AnalysisCacheIntegrityTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'));guard.start();self.addCleanup(guard.stop)
    def test_a_valid_cache_is_retained_without_changing_the_task(self):
        task,facts,_=fixture();before=canonical(task)
        self.assertEqual(current_analysis(task,facts),task['draft_analysis']);self.assertEqual(canonical(task),before)
    def test_one_corrupt_result_does_not_remove_other_valid_results(self):
        for field,value in [('value',999),('display','999.00'),('exact','999'),('inputs',dict(a=1,b=120)),
                            ('method_version',999),('period','20260630'),('data_refs',['D2','D5']),
                            ('financial_approval',True),('condition','condition changed')]:
            with self.subTest(field=field):
                task,facts,_=fixture();bad=task['draft_analysis']['results'][0];ident=bad['id'];bad[field]=value
                before=canonical(task);valid=current_analysis(task,facts)['results']
                self.assertNotIn(ident,{row['id'] for row in valid});self.assertEqual(len(valid),len(task['draft_analysis']['results'])-1)
                self.assertEqual(canonical(task),before)
    def test_invalid_exact_values_cannot_crash_or_provide_a_false_growth(self):
        for value in (None,'not-a-number','NaN','Infinity'):
            with self.subTest(value=value):
                task,facts,proposal=fixture();task['draft_analysis']['results'][0]['exact']=value
                result,_=prepare_draft_client_copy(task,proposal,facts)
                self.assertNotIn('营业收入同比20.00%',result['conclusion']);self.assertIn('单位未明',result['conclusion'])
    def test_duplicate_result_ids_are_not_resolved_by_picking_the_first(self):
        task,facts,_=fixture();item=deepcopy(task['draft_analysis']['results'][0]);task['draft_analysis']['results'].append(item)
        valid=current_analysis(task,facts)['results'];self.assertNotIn(item['id'],{row['id'] for row in valid})
        self.assertTrue(valid)
    def test_malformed_cache_results_are_not_supplied_to_the_writer(self):
        for values in (None,{},'invalid',[None]):
            with self.subTest(values=values):
                task,facts,_=fixture();task['draft_analysis']['results']=values
                context=client_writing_context(task,facts);self.assertEqual(context['calculations'],[])
                self.assertTrue(context['observations'])
    def test_an_empty_valid_analysis_remains_a_valid_readonly_cache(self):
        task,facts,_=fixture();facts=[];task['draft_analysis']=analyze(task,facts)
        self.assertEqual(current_analysis(task,facts),task['draft_analysis'])
    def test_only_valid_calculations_reach_the_app_and_host_writing_context(self):
        task,facts,proposal=fixture();task['draft_analysis']['results'][0].update(value=999,display='999.00',exact='999')
        context=client_writing_context(task,facts);self.assertNotIn('A1',{row['id'] for row in context['calculations']})
        self.assertTrue(context['calculations']);result,_=prepare_draft_client_copy(task,proposal,facts)
        self.assertNotIn('999.00%',result['conclusion']);self.assertIn('单位未明',result['conclusion'])
    def test_host_recovery_recomputes_locally_and_still_saves_the_report(self):
        with tempfile.TemporaryDirectory(prefix='researchhelper-host-cache-') as folder:
            session=Session(folder);self.addCleanup(session.agent.close_runtime)
            task=session.create('解释600000.SH营业收入变化的投资含义并生成报告',entry='个股',objects=['600000.SH'],research_mode='current')
            wire,_,_=fixture();records=wire['source_records']
            for record in records:
                record.update(task_id=task['id'],research_version=task['version'],content_version=1,obtained_at=task['cutoff']);record['parameters']['codes']='600000.SH'
                record['snapshot']['tables'][0]['thscode']='600000.SH';rehash(record)
            task['synthetic']=True;task['source_records']=records;session.archive.save(task)
            task=session.draft(task['id'],defer_delivery=True,data_requests=[])
            task['draft_analysis']['results'][0].update(value=999,display='999.00',exact='999');session.archive.save(task)
            context=session.tools(task['id'])['first_report']['writing_context']
            self.assertNotIn('A1',{row['id'] for row in context['calculations']})
            done=session.draft(task['id'],report=dict(title='收入变化',conclusion='营业收入由100元升至120元。',
                logics=[dict(title='收入变化',analysis='比较同口径收入。',data_refs=[row['id'] for row in task['draft_observations'] if row['field']=='ths_revenue_stock'])]))
            self.assertTrue(done.get('delivery'),done.get('error'));self.assertIn('20.00%',done['draft_report']['conclusion'])
            self.assertNotIn('999.00%',done['draft_report']['conclusion']);self.assertEqual(done['ledger']['used']['model_calls'],0);self.assertEqual(done['ledger']['used']['data_requests'],0)


if __name__=='__main__':unittest.main()
