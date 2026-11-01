"""Source-local literal unit references, without automatic unit assignment."""
import copy
from pathlib import Path
import tempfile
import unittest

from tests import ROOT
from tests.test_asset_data_acceptance_review import load_retained_task
from tests.test_operating_original_protocol import payload
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.planning import input_fingerprint
from researchhelper.reports import compose
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS
from researchhelper.output_schema import (_explicit_original_unit_references, _original_span_choices,
    original_span_output_schema, original_span_prompt, decode_original_spans,
    provider_request_schema, ORIGINAL_SPAN_PROTOCOL)


POLICY = 'original_explicit_unit_reference_version'


class ExplicitOriginalUnitTests(unittest.TestCase):
    def setUp(self):
        self.folder=ROOT/'result/acceptance/autonomous-data-phase-7gu8uc_u'
        self.original=load_retained_task(self.folder);self.before=fingerprint(self.original)
        self.task=copy.deepcopy(self.original)
        # Isolated explicit-code protocol fixture. Do not forge a fresh
        # name-query receipt after changing a versioned task policy.
        self.task['objects']=['600030.SH'];self.task[POLICY]=1
        self.task['input_hash']=input_fingerprint(self.task)
        self.views=self.task['reasoning_state']['reading']['bundle']['views']
        self.schema=original_span_output_schema(self.task,self.views)
        self.choice=_original_span_choices(self.task,self.views)[0]

    def tearDown(self):
        self.assertEqual(fingerprint(load_retained_task(self.folder)),self.before)

    def wire(self):
        properties=next(v['properties'] for v in self.schema['properties']['selections']['items']['anyOf']
                        if v['properties']['kind']['enum']==['number'])
        link=properties['review']['properties']['need_links']['items']['anyOf'][0]['properties']
        unit=next(u for u in self.choice['unit_references'] if u['unit']=='元'
                  and self.choice['lines'][u['line']]['part']=='第11页')
        period=next(p for p in self.choice['period_anchors'] if p['period']=='2026-01-01/2026-06-30')
        items=[]
        for ident,value in (('revenue','49,691,932,907.10'),('parent_profit','23,343,231,403.68')):
            token=next(t for t in self.choice['number_tokens'] if t['token']==value
                       and self.choice['lines'][t['line']]['part']=='第11页')
            items.append(dict(id='ev_'+ident,kind='number',source_ref=self.choice['source_ref'],
                object='600030.SH',nature='已实现事实',temporal_reference=dict(period_ref=period['ref']),
                number_reference=dict(number_ref=token['ref'],unit_ref=unit['ref']),
                review=dict(scope='current_original_descriptive_only',need_links=[dict(
                    question_id=link['question_id']['enum'][0],need_ids=[link['need_ids']['items']['enum'][0]])])))
        return dict(version=12,binding_ref=self.schema['properties']['binding_ref']['enum'][0],selections=items)

    def test_longest_units_not_currency_suffixes_or_compound_fragments(self):
        text='单位：人民币百万元，千万元，亿元，万元，千元，元；GWh MWh kWh。'
        refs=_explicit_original_unit_references([dict(index=8,text=text)])
        self.assertEqual([r['unit'] for r in refs],['百万元','千万元','亿元','万元','千元','元','GWh','MWh','kWh'])
        self.assertTrue(all(r['line']==8 and r['unit'] in text for r in refs))
        for compound in ('元/股','GWh/MWh','% /年','美元','港元','新加坡元','万元／年','万亿元','someGWh'):
            self.assertEqual(_explicit_original_unit_references([dict(index=0,text=compound)]),[],compound)

    def test_amount_and_unit_addresses_expand_to_exact_originals_not_approval(self):
        specs=decode_original_spans(self.wire(),self.schema,(self.task,self.views))['evidence_specs']
        self.assertEqual([s['raw_token'] for s in specs],['49,691,932,907.10','23,343,231,403.68'])
        self.assertTrue(all(s['unit']==s['raw_unit']=='元' and s['conversion_factor']==1 for s in specs))
        self.assertTrue(all(s['record_locator']['unit_part']=='第11页' for s in specs))
        self.assertTrue(all(s['period']=='2026-01-01/2026-06-30' for s in specs))
        self.assertFalse(any('source_check' in s or 'calculation_context' in s for s in specs))

    def test_unknown_unit_ref_and_mixed_branches_are_rejected(self):
        for mutation in (dict(unit_ref='u99999'),dict(unit='元'),dict(quantity_ref='v1')):
            wire=self.wire();wire['selections'][0]['number_reference'].update(mutation)
            with self.subTest(mutation=mutation),self.assertRaises(ContractError):
                decode_original_spans(wire,self.schema,(self.task,self.views))

    def test_old_tasks_keep_old_schema_and_no_guessed_unit_assignment(self):
        old=original_span_output_schema(self.original,self.views)
        self.assertNotIn('unit_ref"',canonical(old))
        self.assertIn('unit_ref"',canonical(self.schema))
        self.assertTrue(all(set(u)=={'ref','line','unit'} for u in self.choice['unit_references']))
        self.assertTrue(all('number_ref' not in u for u in self.choice['unit_references']))

    def test_prompt_retains_all_text_and_actual_unit_lines_with_same_capacity(self):
        shown=original_span_prompt(payload(self.task,self.views),self.task)
        view=shown['untrusted_sources'][0]
        self.assertEqual(view['unit_reference_columns'],['unit_ref','line','literal_unit'])
        self.assertEqual(len(view['unit_references']),len(self.choice['unit_references']))
        for ref,line,unit in view['unit_references']:
            self.assertIn(unit,self.choice['lines'][line]['text'])
        wire_schema=provider_request_schema(self.task,self.schema,ORIGINAL_SPAN_PROTOCOL)
        self.assertLessEqual(len(canonical(shown))+len(canonical(wire_schema)),MAX_CONTEXT_CHARACTERS)

    def test_unknown_policy_is_not_treated_as_enabled(self):
        for value in (True,'1',2):
            task=dict(self.task);task[POLICY]=value
            with self.subTest(value=value),self.assertRaises(ContractError):
                _original_span_choices(task,self.views)

    def test_new_policy_is_input_bound_and_report_frozen(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance') as folder:
            agent=ResearchAgent(ResearchArchive(Path(folder)),ROOT,offline=True,require_argument_support=True)
            try:
                task=agent.create('解释ETF折溢价',entry='ETF',questions=[dict(id='q1',kind='fact',
                    question='解释ETF折溢价',answer_scope='general_definition',knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task[POLICY],1)
                old=dict(task);old.pop(POLICY)
                self.assertNotEqual(input_fingerprint(old),task['input_hash'])
                self.assertEqual(compose(agent.start(task['id']))[POLICY],1)
            finally:agent.close_runtime()


if __name__=='__main__':
    unittest.main()
