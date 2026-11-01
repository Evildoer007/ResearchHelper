"""Failed or changed source records never become first-report business data."""
from copy import deepcopy
import unittest
from researchhelper.contracts import fingerprint
from researchhelper.draft_sources import observations,source_context,writing_context,source_issues
from tests.test_draft_source_types import record,task_for


def response(ident='source',value=42):
    source=record(dict(errorcode=0,tables=[dict(thscode='600519.SH',time=['2026-10-30'],table=dict(close=[value],主营业务=['研发与销售']))]))
    source.update(id=ident,kind='api',status='成功待核验')
    source['hash']=fingerprint(source['snapshot'])
    source['request_hash']=fingerprint({key:source[key] for key in ('source','operation','parameters')})
    return source


class DraftSourceIntegrityTests(unittest.TestCase):
    def test_failed_source_is_quarantined_without_losing_an_independent_response(self):
        failed=response('bad',98765);failed['status']='业务失败待按官方错误定义核查'
        good=response('good',42);task=task_for(failed,good);before=deepcopy(task)
        facts=observations(task)
        self.assertEqual([(row['artifact_id'],row['raw_value']) for row in facts],[('good',42)])
        self.assertEqual(source_issues(task)[0]['artifact_id'],'bad')
        self.assertEqual(task,before)

    def test_nonzero_or_boolean_errorcode_cannot_hide_behind_a_success_label(self):
        for code in (-1,'-1',False,True,None):
            source=response();source['snapshot']['errorcode']=code;source['hash']=fingerprint(source['snapshot'])
            with self.subTest(code=code):self.assertEqual(observations(task_for(source)),[])

    def test_changed_snapshot_or_request_is_not_silently_accepted(self):
        for field in ('snapshot','parameters','snapshot_hash'):
            source=response()
            if field=='snapshot':source['snapshot']['tables'][0]['table']['close'][0]=98765
            elif field=='parameters':source['parameters']['codes']='000001.SZ'
            else:source['snapshot_hash']='changed'
            with self.subTest(field=field):
                self.assertEqual(observations(task_for(source)),[])
                self.assertTrue(source_issues(task_for(source)))

    def test_explicit_old_input_cannot_supply_current_observations(self):
        source=response();source['input_hash']='old';task=dict(task_for(source),input_hash='current')
        self.assertEqual(observations(task),[])

    def test_planner_receives_failure_metadata_without_the_residual_business_body(self):
        source=response(value=98765);source['status']='返回结构异常'
        context=source_context(task_for(source))
        self.assertEqual(context[0]['content'],'');self.assertTrue(context[0]['excluded_reason'])
        self.assertNotIn('98765',str(context))

    def test_writer_cannot_reintroduce_failed_data_through_other_data_or_stale_facts(self):
        source=response(value=98765);task=task_for(source);stale=observations(task)
        source['status']='业务失败待按官方错误定义核查'
        context=writing_context(task,stale)
        self.assertEqual(context,dict(observations=[],sources=[],documents=[],other_data=[]))

    def test_unmatched_business_text_is_projected_without_rewriting_the_original_hash(self):
        source=response();task=task_for(source);before=deepcopy(task)
        context=writing_context(task,observations(task))
        self.assertIn('研发与销售',context['other_data'][0]['content'])
        self.assertNotIn('"close"',context['other_data'][0]['content'])
        self.assertEqual(task,before);self.assertEqual(source_issues(task),[])

    def test_missing_optional_integrity_metadata_keeps_legacy_input_unverified(self):
        source=response();source.pop('hash');source.pop('request_hash');source.pop('kind');source.pop('status')
        facts=observations(task_for(source))
        self.assertEqual(facts[0]['raw_value'],42);self.assertEqual(facts[0]['status'],'原始取数')
        self.assertFalse(facts[0].get('financial_approval'))

    def test_zero_and_missing_values_in_a_good_response_keep_their_original_dates(self):
        source=response(value=0);source['snapshot']['tables'][0]['time'].append('2026-10-31')
        source['snapshot']['tables'][0]['table']['close'].append(None);source['hash']=fingerprint(source['snapshot'])
        facts=observations(task_for(source));self.assertEqual([row['raw_value'] for row in facts],[0,None])


if __name__=='__main__':unittest.main()
