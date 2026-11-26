"""Cited cross-sections keep exact point provenance before display ranking."""
from copy import deepcopy
import unittest
from tests import ROOT
from researchhelper.drafts import draft_figures
from researchhelper.draft_visuals import logic_figure_plan
from tests.accept_official_observation_package import frontend_fixture

class CrossSectionArgumentFiguresTests(unittest.TestCase):
    def setUp(self):
        task=frontend_fixture();facts=task['draft_report']['facts']
        self.content=dict(task_id=task['id'],research_version=task['version'],cutoff=task['cutoff'],
            source_records=task['source_records'],draft=dict(facts=facts,calculations=[],
                argument_visual_scope_version=1,logics=[]))
    def logic(self,refs):
        self.content['draft']['logics']=[dict(title='成分差异',analysis='比较实际成分的权重。',data_refs=refs)]
        return logic_figure_plan(self.content)
    def test_global_top_list_keeps_exact_ordered_point_references(self):
        figures=draft_figures(self.content['draft']['facts']);figure=figures[0]
        facts={row['id']:row for row in self.content['draft']['facts']}
        self.assertEqual(len(figure['data_refs']),15)
        self.assertEqual(figure['values'],[facts[ref]['value'] for ref in figure['data_refs']])
        self.assertEqual(figure['dates'],[facts[ref]['object_label'] for ref in figure['data_refs']])
        self.assertEqual(figure['original_rows'],300)
    def test_full_cited_cross_section_has_a_provenance_bound_figure(self):
        refs=[row['id'] for row in self.content['draft']['facts'][1:]]
        figures=self.logic(refs)
        self.assertEqual(len(figures),1);self.assertEqual(len(figures[0]['data_refs']),15)
        self.assertEqual(figures[0]['paper_logic'],'logic-1')
        self.assertTrue(set(figures[0]['data_refs'])<=set(refs))
        self.assertEqual(figures[0]['point_refs'],[[ref] for ref in figures[0]['data_refs']])
    def test_three_constituents_outside_the_global_top15_still_get_their_own_figure(self):
        top=set(draft_figures(self.content['draft']['facts'])[0]['data_refs'])
        refs=[row['id'] for row in self.content['draft']['facts'][1:] if row['id'] not in top][:3]
        figures=self.logic(refs)
        self.assertEqual(len(figures),1);self.assertEqual(set(figures[0]['data_refs']),set(refs))
        self.assertNotIn('前15项',figures[0]['name'])
        self.assertEqual(self.logic(refs[:2]),[])
    def test_one_point_or_unavailable_values_do_not_make_an_empty_chart(self):
        self.assertEqual(self.logic(['D2']),[])
        self.content['draft']['facts'][2]['value']=None
        self.assertEqual(self.logic(['D2','D3']),[])
    def test_unknown_refs_or_foreign_sources_cannot_supply_chart_points(self):
        for figure in self.logic(['unknown']):
            self.assertNotIn('unknown',figure.get('data_refs',[]))
        self.content['source_records'][1]['task_id']='foreign'
        self.assertEqual(self.logic(['D2','D3']),[])
    def test_input_snapshot_and_all_unplotted_values_remain_unchanged(self):
        before=deepcopy(self.content);self.logic(['D2','D3'])
        # Only the authored argument was assigned by the fixture itself.
        before['draft']['logics']=self.content['draft']['logics']
        self.assertEqual(self.content,before)
        self.assertEqual(len(self.content['draft']['facts']),301)
    def test_identical_source_revisions_do_not_create_duplicate_cross_sections(self):
        source=deepcopy(self.content['source_records'][1]);source['id']='independent'
        self.content['source_records'].append(source)
        first=self.content['draft']['facts'][1:4]
        second=[dict(row,id='other-'+row['id'],artifact_id='independent') for row in first]
        self.content['draft']['facts'].extend(second)
        figures=self.logic([row['id'] for row in first+second])
        figure, = figures
        self.assertIn(figure['source_ref'],{first[0]['artifact_id'],'independent'})
        expected = first if figure['source_ref'] == first[0]['artifact_id'] else second
        self.assertEqual(set(figure['data_refs']),{row['id'] for row in expected})
        self.assertTrue(all(row in self.content['draft']['facts'] for row in first+second))

    def test_distinct_dates_and_bases_are_not_merged_into_one_ranking(self):
        rows=self.content['draft']['facts'];extra=dict(rows[1],id='other',period='2026-10-20')
        rows.append(extra)
        figures=self.logic(['D2','other'])
        self.assertEqual(figures,[])

if __name__=='__main__':unittest.main()
