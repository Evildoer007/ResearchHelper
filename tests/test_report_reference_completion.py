"""Missing optional citations keep frozen prose readable without new approval."""
from copy import deepcopy
import unittest
from lxml import html
from tests import ROOT
from researchhelper.contracts import ContractError,fingerprint
from researchhelper.reports import compose,render
from tests import test_document_content_offline as documents


class ReportReferenceCompletionTests(unittest.TestCase):
    def setUp(self):
        self.fixture=documents.DocumentContentTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.agent=self.fixture.agent
        task=self.fixture.material_task()
        self.done=self.fixture.run_spec(task,self.fixture.spec(task));self.content=compose(self.done)

    def paper(self,content,profile='standard'):
        content['content_hash']=fingerprint({key:value for key,value in content.items() if key!='content_hash'})
        before=deepcopy(content);tree=html.document_fromstring(render(content,ROOT,report_profile=profile))
        self.assertEqual(content,before)
        return tree

    def test_one_missing_source_keeps_answer_and_valid_citation_in_both_profiles(self):
        for profile in ('standard','detailed'):
            with self.subTest(profile=profile):
                content=deepcopy(self.content);question=content['questions'][0]
                question['refs'].append('missing-original')
                tree=self.paper(content,profile)
                self.assertIn(question['answer'],tree.xpath('//main')[0].text_content())
                self.assertFalse(tree.xpath('//*[@href="#ev-missing-original"]'))
                self.assertTrue(tree.xpath('//*[@id="ev-'+question['refs'][0]+'"]'))
                notes=tree.xpath('//*[@data-inspection-title="引用说明"]')
                self.assertEqual(len(notes),1);self.assertIn('missing-original',notes[0].text_content())
                self.assertFalse(notes[0].xpath('./h3'));self.assertEqual(notes[0].get('aria-label'),'引用说明')
                self.assertNotIn('missing-original',tree.xpath('//main')[0].text_content())

    def test_all_missing_references_are_one_escaped_note_without_a_fake_source(self):
        content=deepcopy(self.content);question=content['questions'][0]
        hostile='missing"><img src=x onerror="window.unwanted=1">'
        question['refs']=['missing-original',hostile,'missing-original'];content['evidence']=[]
        question['calculation_refs']=['missing-calculation']
        tree=self.paper(content)
        self.assertIn(question['answer'],tree.xpath('//main')[0].text_content())
        notes=tree.xpath('//*[@data-inspection-title="引用说明"]')
        self.assertEqual(len(notes),1)
        self.assertIn(hostile,notes[0].text_content())
        self.assertEqual(notes[0].text_content().count('missing-original'),1)
        self.assertIn('missing-calculation',notes[0].text_content())
        self.assertFalse(tree.xpath('//img[@src="x"]|//*[@href="#missing-calculation"]'))

    def test_thesis_support_and_counter_notes_do_not_remove_the_original_logic(self):
        done=self.agent.start(self.agent.create_case('mechanism')['id']);content=compose(done)
        thesis=content['theses'][0]
        thesis['support'].append('missing-support');thesis.setdefault('counter',[]).append('missing-counter')
        tree=self.paper(content,'detailed')
        self.assertIn(thesis['title'],tree.xpath('//main')[0].text_content())
        self.assertEqual(len(tree.xpath('//*[@data-inspection-title="引用说明"]')),2)
        self.assertFalse(tree.xpath('//*[@href="#ev-missing-support"]|//*[@href="#ev-missing-counter"]'))

    def test_rendering_cannot_approve_broken_composition_or_a_changed_content_hash(self):
        original=self.agent.archive.get(self.done['id']);changed=deepcopy(original)
        changed['questions'][0]['refs'].append('missing-original')
        with self.assertRaises(ContractError):compose(changed)
        altered=deepcopy(self.content);altered['questions'][0]['answer']='Changed without freezing'
        with self.assertRaises(ContractError):render(altered,ROOT)
        self.assertEqual(self.agent.archive.get(self.done['id']),original)


if __name__=='__main__':unittest.main()
