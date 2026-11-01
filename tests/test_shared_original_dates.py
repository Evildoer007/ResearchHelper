"""Lossless metadata sharing for the actual over-capacity ETF originals."""
import copy
import gzip
import json
import unittest
from tests import ROOT
from tests.test_operating_original_protocol import payload
from researchhelper.contracts import canonical
from researchhelper.planning import input_fingerprint
from researchhelper.output_schema import (original_span_output_schema,original_span_prompt,
    provider_request_schema,ORIGINAL_SPAN_PROTOCOL,_original_span_choices)
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS


class SharedOriginalDatesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with gzip.open(ROOT/'result/acceptance/autonomous-data-phase-ae742kg8/task.json.gz','rt',encoding='utf-8') as stream:
            cls.old=json.load(stream)
        cls.views=cls.old['reasoning_state']['reading']['bundle']['views']
        cls.task=copy.deepcopy(cls.old)
        cls.task['shared_original_dates_version']=1
        cls.task['input_hash']=input_fingerprint(cls.task)
        def prompt(task):
            request=payload(task,cls.views);bundle=task['reasoning_state']['reading']['bundle']
            request['source_reading']={key:bundle[key] for key in ('reading_hash','coverage','limitations')}
            request['calculation_input_targets']=request['evidence_contract']['calculation_input_requirements']['proposal_requirements']
            return original_span_prompt(request,task)
        cls.before=prompt(cls.old);cls.after=prompt(cls.task)

    def test_complete_pair_still_needs_batches_but_each_full_source_fits_the_same_capacity(self):
        oldsize=len(canonical(self.before))
        schema=original_span_output_schema(self.task,self.views)
        newsize=len(canonical(self.after))+len(canonical(provider_request_schema(self.task,schema,ORIGINAL_SPAN_PROTOCOL)))
        self.assertGreater(oldsize,MAX_CONTEXT_CHARACTERS)
        self.assertGreater(newsize,MAX_CONTEXT_CHARACTERS)
        self.assertLess(newsize,oldsize)
        for view in self.views:
            shown=original_span_prompt(payload(self.task,[view]),self.task)
            one_schema=original_span_output_schema(self.task,[view])
            self.assertLessEqual(len(canonical(shown))+len(canonical(provider_request_schema(
                self.task,one_schema,ORIGINAL_SPAN_PROTOCOL))),MAX_CONTEXT_CHARACTERS)
        self.assertEqual(len(self.before['untrusted_sources']),2)
        for old,new in zip(self.before['untrusted_sources'],self.after['untrusted_sources']):
            self.assertEqual(old['parts'],new['parts'])
            self.assertEqual(old['unit_references'],new['unit_references'])

    def test_all_heterogeneous_date_fields_and_original_indices_are_reconstructable(self):
        choices=_original_span_choices(self.task,self.views)
        for view,choice in zip(self.after['untrusted_sources'],choices):
            self.assertEqual(view['date_anchor_encoding'],'grouped_exact_columns_v1')
            restored={}
            for table in view['date_anchor_tables']:
                for row in table['rows']:
                    value=dict(zip(table['columns'],row));index=value.pop('index')
                    part=view['date_part_labels'][value.pop('part_ref')]
                    restored[index]=dict(value,part=part)
            self.assertEqual([restored[i] for i in range(len(restored))],choice['description']['dates'])
            source=next(s for s in self.after['evidence_contract']['sources'] if s['artifact_id']==view['id'])
            ref=source['original_date_anchors_ref']
            self.assertEqual([restored[i] for i in ref['indices']],choice['description']['source']['original_date_anchors'])

    def test_observation_eligibility_is_not_changed_by_table_sharing(self):
        for before,after in zip(self.before['untrusted_sources'],self.after['untrusted_sources']):
            for field in ('observation_anchors','non_observation_dates'):
                rows=before.get(field,[]);columns=before.get('observation_anchor_columns')
                indices=[r['index'] if isinstance(r,dict) else r[columns.index('index')] for r in rows]
                self.assertEqual(indices,after[field]['indices'])


if __name__=='__main__':unittest.main()
