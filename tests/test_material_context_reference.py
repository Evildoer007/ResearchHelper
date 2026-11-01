"""Lossless material context references, not inferred financial evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, METHOD_REGISTRY, canonical, fingerprint
from researchhelper.engine import ResearchAgent
import researchhelper.engine as engine_module
import researchhelper.reasoning as reasoning
from researchhelper.planning import question_specs


class MaterialContextReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='material-context-fixture-')
        self.addCleanup(temporary.cleanup)
        origin = getattr(engine_module, '__frozen_origin__', None)
        resources = Path(origin.split('!/PYZ.pyz/')[0]).parents[2] if origin else None
        self.agent = ResearchAgent(ResearchArchive(Path(temporary.name) / 'store'), resources)
        self.addCleanup(self.agent.close_runtime)
        guard = patch('socket.socket.connect', side_effect=AssertionError('No external calls in this test'))
        guard.start(); self.addCleanup(guard.stop)
        task = self.agent.create('核对同口径净利润率，不作投资判断', entry='个股', objects=['合成测试主体'],
            cutoff='2026-10-02T18:00:00+08:00', questions=[dict(id='q1', question='核对同口径净利润率，不作投资判断',
                kind='fact', method_needs=['net_profit_margin_v1'])])
        self.specs = []
        for metric, value, label in [('revenue', 100, '营业收入'), ('net_profit', 12, '净利润')]:
            text = f'合成测试主体，2026年上半年，人民币，企业会计准则，合并利润表：{label}{value}百万元。发布于2026-10-01。'
            task = self.agent.add_material(task['id'], metric + '.txt', text.encode())
            material = task['materials'][-1]
            basis = dict(kind='material', artifact_id=material['id'], snapshot_hash=material['snapshot_hash'],
                         part='段落1', quote=text)
            context = dict(entity_id='合成测试主体', currency='CNY', period='2026年上半年',
                           accounting_scope='consolidated', accounting_standard='CAS', metric=metric)
            self.specs.append(dict(id='ev_' + metric, artifact_id=material['id'], object='合成测试主体',
                record_locator=dict(part='段落1'), text=text, period='2026年上半年', nature='已实现事实',
                published_at='2026-10-01', publication=dict(at='2026-10-01', basis='原件明确发布日',
                    precision='日', timezone='Asia/Shanghai', certainty='已确认', basis_ref=copy.deepcopy(basis)),
                raw_value=value, value=value, raw_token=str(value), raw_unit='百万元', unit='百万元',
                conversion_factor=1, question_ids=['q1'], calculation_context=context,
                calculation_context_basis={key:copy.deepcopy(basis) for key in context},
                review=dict(question_ids=['q1'], allowed_natures=['已实现事实'],
                    conditions=['仅核对材料明确的主体、期间与合并范围'], reason='合成验收，不作为真实财务事实')))
        catalogue = self.agent.source_catalog(task['id'])
        requests = [dict(item['request_binding'], queries=['合成测试主体'], parts=[],
            reason='核对本问题登记方法的全部输入口径', question_ids=['q1']) for item in catalogue['items']]
        self.reading = self.agent.read_materials(task['id'], requests, catalogue['binding'])
        self.task = self.agent.archive.get(task['id'])
        self.views = self.reading['views']

    def compact(self, specs=None):
        result = copy.deepcopy(self.specs if specs is None else specs)
        sources = {item['artifact_id']:item for item in reasoning.evidence_contract(self.task, self.views)['sources']}
        for item in result:
            full = item.pop('calculation_context_basis')
            item['material_context_reference'] = dict(
                candidate_id=sources[item['artifact_id']]['material_context_reference']['candidate_id'],
                bases={key:{name:reference[name] for name in ('part','quote')} for key,reference in full.items()})
        return result

    def expand(self, specs=None, task=None, views=None):
        return reasoning.expand_material_context_references(task or self.task,
            self.compact() if specs is None else specs, self.views if views is None else views)

    def host(self, specs):
        definition = METHOD_REGISTRY['net_profit_margin_v1']
        return dict(reading_hash=self.reading['reading_hash'], evidence_specs=specs,
            calculations=[dict(id='profit_margin', method='net_profit_margin_v1',
                inputs={'revenue':'ev_revenue','net_profit':'ev_net_profit'},
                parameters=dict(definition['parameters'], period='2026年上半年',
                    accounting_scope='consolidated', accounting_standard='CAS'), question_ids=['q1'])],
            answers={'q1':dict(refs=['ev_revenue','ev_net_profit'], calculation_refs=['profit_margin'],
                numeric_segments=dict(answer=[dict(text='同口径净利润率为'),
                    dict(source='calculation',ref='profit_margin',path='value',format='signed',digits=2),
                    dict(text='，不代表未来收益或买卖建议。')]))})

    def test_expansion_is_exact_and_does_not_mutate_any_input(self):
        compact = self.compact()
        before = fingerprint((self.task, compact, self.views))
        self.assertEqual(self.expand(compact), self.specs)
        self.assertEqual(fingerprint((self.task, compact, self.views)), before)
        self.assertEqual(self.task['evidence'], [])
        self.assertLess(len(canonical(compact)), len(canonical(self.specs)))

    def test_task_version_cutoff_and_input_changes_reject_old_reference(self):
        specs = self.compact()
        for field,value in [('id','foreign_task'),('version',2),('cutoff','2026-10-03T18:00:00+08:00'),('input_hash','changed')]:
            with self.subTest(field=field):
                task = copy.deepcopy(self.task); task[field] = value
                with self.assertRaises(ContractError): self.expand(specs, task=task)

    def test_missing_duplicate_changed_or_unread_views_are_rejected(self):
        specs = self.compact()
        variants = [[], self.views + [self.views[0]], copy.deepcopy(self.views), copy.deepcopy(self.views)]
        variants[2][0]['snapshot_hash'] = 'changed'
        variants[3][0]['parts'] = []
        for views in variants:
            with self.subTest(views=len(views)):
                with self.assertRaises(ContractError): self.expand(specs, views=views)
        with self.assertRaises(ContractError):
            reasoning.expand_material_context_references(self.task, specs)

    def test_alias_cannot_mix_with_full_or_api_context(self):
        for field,value in [('calculation_context_basis',self.specs[0]['calculation_context_basis']),
                            ('api_context_reference',{})]:
            specs = self.compact(); specs[0][field] = value
            with self.subTest(field=field),self.assertRaises(ContractError): self.expand(specs)

    def test_quote_locator_and_basis_fields_are_not_repaired(self):
        for key,value in [('part','未读段落'),('quote','原文没有的币种'),('extra','not_allowed')]:
            specs = self.compact(); specs[0]['material_context_reference']['bases']['currency'][key] = value
            with self.subTest(key=key),self.assertRaises(ContractError): self.expand(specs)
        specs = self.compact(); del specs[0]['material_context_reference']['bases']['currency']
        with self.assertRaises(ContractError): self.expand(specs)

    def test_unknown_wrong_source_and_malformed_ids_are_rejected(self):
        for value in ['',None,{},'rh_ctx_forged',self.compact()[1]['material_context_reference']['candidate_id']]:
            specs = self.compact(); specs[0]['material_context_reference']['candidate_id'] = value
            with self.subTest(value=value),self.assertRaises(ContractError): self.expand(specs)
        specs = self.compact(); specs[0]['material_context_reference']['approved'] = True
        with self.assertRaises(ContractError): self.expand(specs)

    def test_context_values_are_not_inferred_or_financially_approved(self):
        specs = self.compact(); specs[0]['calculation_context']['currency'] = 'USD'
        expanded = self.expand(specs)
        self.assertEqual(expanded[0]['calculation_context']['currency'], 'USD')
        result = self.agent.start(self.task['id'], self.host(specs))
        self.assertFalse(result['calculations'])
        self.assertFalse(any(item.get('calculation_context',{}).get('currency')=='USD' for item in result['evidence']))
        self.assertNotEqual(result['status'], '已完成')

    def test_full_canonical_format_remains_unchanged(self):
        self.assertIs(reasoning.expand_material_context_references(self.task, self.specs), self.specs)
        self.assertEqual(self.agent._validate_host_input(self.task, self.host(self.specs))['evidence_specs'],self.specs)

    def test_ordinary_model_decodes_before_validation_and_records_wire_hash(self):
        compact = self.compact()
        class Model:
            def reason_phase(self,task,messages,phase):
                self.messages = copy.deepcopy(messages)
                return json.dumps({'evidence_specs':compact},ensure_ascii=False)
        model = Model()
        payload = dict(question=self.task['question'], intent=self.task['intent'],
            questions=question_specs(self.task), untrusted_sources=self.views,
            evidence_contract=reasoning.evidence_contract(self.task,self.views))
        output,receipt = reasoning.model_object(model,self.task,'extract',payload,{'evidence_specs'})
        self.assertEqual(output['evidence_specs'],self.specs)
        self.assertEqual(receipt['material_context_reference_version'],1)
        self.assertEqual(receipt['material_context_wire_hash'],fingerprint({'evidence_specs':compact}))
        self.assertEqual(receipt['output_hash'],fingerprint(output))
        self.assertEqual(self.task['evidence'],[])
        # Publication and context codecs may coexist, but their audit hash
        # must still identify the original wire object, not an intermediate.
        compact = copy.deepcopy(compact)
        sources = {item['artifact_id']:item for item in payload['evidence_contract']['sources']}
        for item in compact:
            item['publication'].pop('basis_ref')
            item['material_publication_candidate_id'] = sources[item['artifact_id']]['material_publication_references'][0]['candidate_id']
        expected = reasoning.expand_material_publication_references(self.task,compact,self.views)
        expected = reasoning.expand_material_context_references(self.task,expected,self.views)
        output,receipt = reasoning.model_object(model,self.task,'extract',payload,{'evidence_specs'})
        self.assertEqual(output['evidence_specs'],expected)
        self.assertEqual(receipt['material_context_wire_hash'],fingerprint({'evidence_specs':compact}))

    def test_host_reaches_same_registered_calculation_and_frozen_report(self):
        result = self.agent.start(self.task['id'],self.host(self.compact()))
        self.assertEqual(result['status'],'已完成',result.get('error'))
        self.assertEqual(result['calculations'][0]['output']['value'],12)
        self.assertTrue(result['delivery'])
        self.assertEqual(result['ledger']['used']['model_calls'],0)
        self.assertEqual(result['ledger']['used']['data_requests'],0)
        self.assertTrue(all('material_context_reference' not in item for item in result['evidence']))


if __name__ == '__main__':
    unittest.main()
