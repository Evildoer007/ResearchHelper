"""One owner for numeric and textual source selections; no implicit facts."""
import copy
import unittest

from tests import test_typed_numeric_selection as numeric_fixtures
from researchhelper.contracts import ContractError
from researchhelper.data_requirements import requirement_slots
from researchhelper.planning import input_fingerprint
from researchhelper.output_schema import original_span_output_schema,decode_original_spans


class TypedOriginalRequirementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture=numeric_fixtures.TypedNumericSelectionTests
        fixture.setUpClass()
        cls.task=copy.deepcopy(fixture.task)
        cls.task['typed_numeric_selection_version']=3;cls.task['input_hash']=input_fingerprint(cls.task)
        cls.views=fixture.views
        cls.choices=fixture.choices
        cls.slots=requirement_slots(cls.task)
        cls.schema=original_span_output_schema(cls.task,cls.views)

    def wire(self):
        return dict(version=12,binding_ref=self.schema['properties']['binding_ref']['enum'][0],selections=[],
            requirements={s['key']:dict(status='not_found',reason='离线选择协议测试，不认定原件没有内容') for s in self.slots})

    def item(self,slot):
        p=self.schema['properties']['requirements']['properties'][slot['key']]['anyOf'][0]['properties']['items']['items']['anyOf'][0]['properties']
        choice=self.choices[p['source_ref']['enum'][0]]
        period=next(d for d in choice['period_anchors'] if d['period']=='2026-01-01/2026-06-30')
        item=dict(id='requirement_test',kind=slot['constraints']['value_kind'],source_ref=choice['source_ref'],
            object=p['object']['enum'][0],nature='合同政策条款',temporal_reference=dict(period_ref=period['ref']),
            review=dict(scope='current_original_descriptive_only'))
        if item['kind']=='number':
            number=next(n for n in choice['number_tokens'] if n['token']=='0.15')
            unit=next(u for u in choice['unit_references'] if u['unit']=='%')
            item['number_reference']=dict(number_ref=number['ref'],unit_ref=unit['ref'])
        else:item['text_reference']={'line':0}
        return item

    def test_all_needs_have_one_typed_owner_and_no_duplicate_inner_association(self):
        self.assertEqual(len(self.slots),len(self.task['questions'][0]['evidence_needs']))
        self.assertEqual(self.schema['properties']['selections']['maxItems'],0)
        for slot in self.slots:
            field=self.schema['properties']['requirements']['properties'][slot['key']]
            for branch in field['anyOf'][0]['properties']['items']['items']['anyOf']:
                p=branch['properties'];self.assertEqual(p['kind']['enum'],[slot['constraints']['value_kind']])
                self.assertNotIn('need_links',p['review']['properties'])
                self.assertNotIn('question_ids',p)

    def test_text_and_number_each_restore_only_the_explicit_outer_requirement(self):
        for kind in ('text','number'):
            slot=next(s for s in self.slots if s['constraints']['value_kind']==kind)
            wire=self.wire();wire['requirements'][slot['key']]=dict(status='found',items=[self.item(slot)])
            spec=decode_original_spans(wire,self.schema,(self.task,self.views))['evidence_specs'][0]
            self.assertEqual(spec['review']['evidence_need_texts'],{slot['question_id']:[slot['text']]})
            self.assertNotIn('source_check',spec)
            if kind=='number':self.assertEqual((spec['value'],spec['unit']),(0.15,'%'))

    def test_old_text_need_ids_and_unscoped_root_items_are_rejected(self):
        slot=next(s for s in self.slots if s['constraints']['value_kind']=='text')
        for wrong in ('inner','root'):
            wire=self.wire();item=self.item(slot)
            if wrong=='inner':item['review']['need_links']=[dict(question_id='q1',need_ids=['n1'])]
            else:wire['selections']=[item]
            wire['requirements'][slot['key']]=dict(status='found',items=[item])
            with self.assertRaises(ContractError):decode_original_spans(wire,self.schema,(self.task,self.views))


if __name__=='__main__':unittest.main()
