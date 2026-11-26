"""Independent installed-Skill price fixture, with fabricated local TLS only."""
import importlib.util
import json
from pathlib import Path
import sys


def run(root, installation, output):
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location('current_price_skill', installation / 'core/session.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from tests.test_authenticated_spawn_workflow_offline import LocalCollector, REFRESH, restrict_network
    from tests.test_question_scope_workflow_offline import price_proposals
    from tests.test_current_planning_workflow_offline import source_fixture_worker
    restrict_network()
    fixed = json.loads((output / 'fixed-input.json').read_text())
    plan = fixed['plan']
    session = module.Session(output / 'project', allow_network=True,
                             source_credentials={'ifind': {'refresh_token': REFRESH}})
    try:
        session.agent.collector = LocalCollector(offline=False, journal=session.agent._journal,
                                                 active=session.agent._check_run_control)
        session.agent.collector.worker_target = source_fixture_worker
        session.agent.collector.original_keeper = session.archive.retain_original
        task = session.create(fixed['question'], entry='个股', objects=['600000.SH'],
                              limits={'data_requests': 4, 'model_calls': 6, 'seconds': 120})
        task = session.plan(task['id'], plan['questions'], '相同固定输入首次分类', task['input_hash'])
        first = session.run(task['id'], data_requests=plan['data_requests'],
                            calculations=plan['calculations'], defer_delivery=True)
        assert first['status'] == '待开始' and not first['delivery'] and first['host_checkpoint']
        proposals = price_proposals(first)
        answers = {'q1': {'refs': ['price_start', 'price_end'], 'calculation_refs': ['price_change'],
            'numeric_segments': {'answer': [
                {'text': '本地合成接口所载两端未复权价格变化为'},
                {'source': 'calculation', 'ref': 'price_change', 'path': 'value', 'format': 'signed', 'digits': 2},
                {'text': '。公司行为未核验，不代表净值收益、持有人总回报或买卖结论。'}]}}}
        second = session.run(task['id'], evidence_specs=proposals, answers=answers,
                             calculations=plan['calculations'], defer_delivery=True)
        assert second['status'] == '待开始' and not second['delivery']
        request = session.argument_review_inputs(task['id'])['request']
        # This explicit fabricated host review exercises the same public contract
        # as the App; it is not a judgment about actual investment quality.
        wire = dict(version=request['version'], review_hash=request['review_hash'],
            questions=[dict(question_id='q1', status='answered', missing_evidence=[],
                reason='核对固定合成输入，不代表金融质量验收')], reviews=[
            dict(target_id=target['id'], segments=[dict(text=target['text'], verdict='supported',
                basis_refs=['calculation:' + target['calculation_refs'][0]] if target['calculation_refs']
                    else ['evidence:' + target['evidence_refs'][0]],
                explanation='固定合成原值与登记计算的协议核对')]) for target in request['targets']])
        session.review_arguments(task['id'], wire)
        second = session.resume(task['id'], defer_delivery=True)
        assert second['questions'][0]['status'] == '已回答', second.get('error') or second['gaps']
        final = session.deliver(task['id'])
        assert final['status'] == '已完成', final.get('error') or final['gaps']
        assert final['questions'][0]['answer'] == fixed['expected_answer']
        assert final['ledger']['used']['model_calls'] == 0 and final['ledger']['used']['data_requests'] == 3
        assert session.agent.model is None and len(final['evidence']) == 2 and len(final['calculations']) == 1
        assert 9.999999 < final['calculations'][0]['output']['value'] < 10.000001
        target = session.export(task['id'])
        stored = session.archive.report(task['id'], 1)
        restored = module.Session(output / 'project')
        try:
            assert restored.archive.report(task['id'], 1) == stored
            assert restored.status(task['id'])['ledger'] == final['ledger']
        finally:
            restored.agent.close_runtime()
        import researchhelper.engine
        assert Path(researchhelper.engine.__file__).is_relative_to(installation)
        for event in final['ledger']['dispatches']:
            if event.get('network_request_started'):
                assert event['network_worker_reaped'] and event['transport_receiver_stopped']
        (output / 'skill-result.json').write_text(json.dumps({
            'status': final['status'], 'answer': final['questions'][0]['answer'],
            'calculation': final['calculations'][0]['output'], 'data_fixture_calls': 3,
            'model_fixture_calls': 0, 'restarted_report_unchanged': True,
            'report_path': str(target), 'model_is_none': True}, ensure_ascii=False, indent=2))
    finally:
        session.agent.close_runtime()


if __name__ == '__main__':
    run(*(Path(value) for value in sys.argv[1:]))
