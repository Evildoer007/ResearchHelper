"""Host plan adoption must not discard a verified, unchanged reading.

Synthetic financial fixtures only. No failed live study is reopened, no
provider receipt is invented, and no original test module is modified.
"""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError,METHOD_REGISTRY
from researchhelper.engine import ResearchAgent
import researchhelper.engine as engine_module
from researchhelper.planning import question_specs
from researchhelper.source_reader import binding,read,validate_reading


class HostReadingPlanAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix='host-reading-adoption-')
        self.addCleanup(self.folder.cleanup)
        # The PYZ audit host is not the native entry point. Resolve resources
        # from that exact verified executable, never from the source checkout.
        origin=getattr(engine_module,'__frozen_origin__',None)
        resources=Path(origin.split('!/PYZ.pyz/')[0]).parents[2] if origin else None
        if resources is not None:self.assertTrue((resources/'app/web').is_dir())
        self.agent=ResearchAgent(ResearchArchive(Path(self.folder.name)/'store'),resources)
        self.addCleanup(self.agent.close_runtime)
        guard=patch('socket.socket.connect',side_effect=AssertionError('No network in this regression'))
        guard.start();self.addCleanup(guard.stop)

    def prepare(self,method='net_profit_margin_v1',*,reader_version=None):
        definition=METHOD_REGISTRY[method]
        question='按同口径核算'+definition['title']+'，不推断投资收益'
        task=self.agent.create(question,entry='个股',objects=['合成测试主体'],
            cutoff='2026-10-02T18:00:00+08:00',
            questions=[dict(id='q1',question=question,kind='fact',method_needs=[method])])
        specifications=[];inputs={}
        values=dict(revenue=100,net_profit=12,operating_cash_flow=8)
        labels=dict(revenue='营业收入',net_profit='净利润',operating_cash_flow='经营活动产生的现金流量净额')
        for field in definition['input_units']:
            statement='合并现金流量表' if field=='operating_cash_flow' else '合并利润表'
            text=f'合成测试主体，2026年上半年，人民币，企业会计准则，{statement}：{labels[field]}{values[field]}百万元。发布于2026-10-01。'
            updated=self.agent.add_material(task['id'],field+'.txt',text.encode())
            material=updated['materials'][-1]
            anchor=dict(kind='material',artifact_id=material['id'],snapshot_hash=material['snapshot_hash'],part='段落1',quote=text)
            context=dict(entity_id='合成测试主体',currency='CNY',period='2026年上半年',
                accounting_scope='consolidated',accounting_standard='CAS',metric=field)
            ident='ev_'+field;inputs[field]=ident
            specifications.append(dict(id=ident,artifact_id=material['id'],object='合成测试主体',
                record_locator=dict(part='段落1'),text=text,period='2026年上半年',nature='已实现事实',
                published_at='2026-10-01',publication=dict(at='2026-10-01',basis='原件明确发布日',
                    precision='日',timezone='Asia/Shanghai',certainty='已确认',basis_ref=anchor),
                raw_value=values[field],value=values[field],raw_token=str(values[field]),raw_unit='百万元',
                unit='百万元',conversion_factor=1,question_ids=['q1'],calculation_context=context,
                calculation_context_basis={key:copy.deepcopy(anchor) for key in context},
                review=dict(question_ids=['q1'],allowed_natures=['已实现事实'],
                    conditions=['仅材料明确的主体、期间与合并范围'],reason='核对同口径原值；仅为合成验收')))
        parameters=dict(definition['parameters'],period='2026年上半年',accounting_scope='consolidated',accounting_standard='CAS')
        host=dict(evidence_specs=specifications,calculations=[dict(id='financial',method=method,inputs=inputs,
            parameters=parameters,question_ids=['q1'])],answers={'q1':dict(refs=list(inputs.values()),
                calculation_refs=['financial'],numeric_segments=dict(answer=[dict(text='同口径计算为'),
                    dict(source='calculation',ref='financial',path='value',format='signed',digits=2),
                    dict(text='，仅描述披露原值，不形成买卖结论。')]))})
        task=self.agent.archive.get(task['id'])
        catalogue=self.agent.source_catalog(task['id'])
        requests=[dict(item['request_binding'],queries=['合成测试主体'],parts=[],
            reason='核对已登记财务方法的原值和全部口径',question_ids=['q1'])
            for item in catalogue['items']]
        if reader_version is None:
            reading=self.agent.read_materials(task['id'],requests,catalogue['binding'])
        else:
            reading=read(task,requests,catalogue['binding'],reader_version=reader_version)
            task.setdefault('source_readings',[]).append(copy.deepcopy(reading))
            self.agent.archive.save(task)
        host['reading_hash']=reading['reading_hash']
        return self.agent.archive.get(task['id']),host,reading

    def complete(self,method='net_profit_margin_v1',*,reader_version=None):
        task,host,reading=self.prepare(method,reader_version=reader_version)
        original=copy.deepcopy(reading)
        result=self.agent.start(task['id'],host)
        self.assertEqual(result['status'],'已完成',result.get('error'))
        self.assertEqual(len(result['calculations']),1)
        self.assertTrue(result['delivery'])
        self.assertEqual(result['ledger']['used']['model_calls'],0)
        self.assertEqual(result['ledger']['used']['data_requests'],0)
        self.assertEqual(reading,original)
        self.assertIn(original,result['source_readings'])
        current=next(item for item in result['source_readings']
                     if item['reading_hash']==result['host_result']['reading_hash'])
        self.assertNotEqual(current['reading_hash'],original['reading_hash'])
        self.assertEqual(current['binding'],binding(result))
        self.assertEqual(current['reader_version'],original['reader_version'])
        for key in ('selection','views','coverage','limitations'):
            self.assertEqual(current[key],original[key])
        validate_reading(result,current)
        with self.assertRaises(ContractError):validate_reading(result,original)
        return result

    def test_net_margin_input_binding_keeps_same_reading(self):self.complete()

    def test_cash_profit_input_binding_keeps_same_reading(self):self.complete('operating_cash_profit_ratio_v1')

    def test_legacy_reader_is_replayed_without_upgrading_it(self):self.complete(reader_version=1)

    def test_old_reading_is_still_rejected_after_a_real_question_change(self):
        task,host,_=self.prepare()
        questions=[dict(question_specs(task)[0],evidence_needs=['另核对会计政策'])]
        self.agent.revise_plan(task['id'],questions,'新增实际研究要求',task['input_hash'])
        with self.assertRaises(ContractError):self.agent.start(task['id'],host)

    def test_tampered_retained_reading_cannot_receive_a_new_binding(self):
        task,host,_=self.prepare()
        task['source_readings'][0]['views'][0]['parts'][0]['text']='伪造正文'
        self.agent.archive.save(task)
        with self.assertRaises(ContractError):self.agent.start(task['id'],host)

    def test_another_task_cannot_adopt_the_reading(self):
        _,host,_=self.prepare()
        other,_unused,_reading=self.prepare()
        with self.assertRaises(ContractError):self.agent.start(other['id'],host)

    def test_new_material_is_not_silently_folded_into_the_old_reading(self):
        task,host,reading=self.prepare()
        updated=self.agent.add_material(task['id'],'extra.txt','新增且尚未阅读的材料。'.encode())
        extra=updated['materials'][-1]['id']
        result=self.agent.start(task['id'],host)
        self.assertEqual(result['status'],'已完成',result.get('error'))
        current=next(item for item in result['source_readings']
                     if item['reading_hash']==result['host_result']['reading_hash'])
        self.assertNotIn(extra,[item['id'] for item in current['views']])
        self.assertEqual(current['views'],reading['views'])

    def test_rejecting_a_reading_does_not_spend_or_start_a_task(self):
        task,host,_=self.prepare()
        host['reading_hash']='unavailable_reading'
        with self.assertRaises(ContractError):self.agent.start(task['id'],host)
        after=self.agent.archive.get(task['id'])
        self.assertEqual(after['ledger'],task['ledger'])
        self.assertEqual(after['status'],'待开始')


if __name__=='__main__':unittest.main()
