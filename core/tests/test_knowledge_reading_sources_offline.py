"""Independent provenance-reading checks; frozen knowledge is never rewritten."""
import copy
import hashlib
from html.parser import HTMLParser
import unittest

from researchhelper.contracts import fingerprint
from researchhelper.knowledge import ENTRIES, RETAINED_VERSIONS
from researchhelper.reports import compose, render
from tests import test_general_definition_answers_offline as fixtures


class KnowledgeBlocks(HTMLParser):
    def __init__(self, markup):
        super().__init__();self.depth=0;self.text=[];self.links=[];self.feed(markup)
    def handle_starttag(self, tag, attrs):
        attributes=dict(attrs)
        if tag=='article' and 'knowledge-reference' in attributes.get('class','').split():self.depth+=1
        elif self.depth and tag=='article':self.depth+=1
        if self.depth and tag=='a':self.links.append(attributes)
    def handle_endtag(self, tag):
        if self.depth and tag=='article':self.depth-=1
    def handle_data(self, text):
        if self.depth:self.text.append(text)


class KnowledgeReadingSourcesTests(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.GeneralDefinitionAnswersTests();fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.agent=fixture.agent
        self.task=self.agent.start(fixture.create()['id'])
        self.content=compose(self.task)
        self.entry=next(item for item in self.content['knowledge']['entries'] if item['id']=='rh_k_etf_measures')

    def test_all_registered_structured_sources_are_readable_links(self):
        block=KnowledgeBlocks(render(self.content,fixtures.ROOT))
        urls={link['href'] for link in block.links}
        for source in [self.entry['source_reference'],*self.entry['additional_source_references']]:
            self.assertIn(source['url'],urls)
        self.assertTrue(all(link.get('target')=='_blank' and 'noopener' in link.get('rel','') for link in block.links))

    def test_conditions_counterexamples_and_time_limit_remain_in_knowledge_block(self):
        block=KnowledgeBlocks(render(self.content,fixtures.ROOT));text=''.join(block.text)
        for field in ('title','content','conditions','counterexample','temporal_scope'):
            self.assertIn(self.entry[field],text)
        for source in [self.entry['source_reference'],*self.entry['additional_source_references']]:
            for section in source['sections']:self.assertIn(section,text)

    def test_rendering_never_changes_authority_task_content_or_saved_delivery(self):
        values=(fingerprint(ENTRIES),fingerprint(self.task),fingerprint(self.content))
        old=self.agent.archive.report(self.task['id'],self.task['version'])
        old_hash=hashlib.sha256(old['html'].encode()).hexdigest()
        render(self.content,fixtures.ROOT)
        self.assertEqual(values,(fingerprint(ENTRIES),fingerprint(self.task),fingerprint(self.content)))
        self.assertEqual(old_hash,hashlib.sha256(self.agent.archive.report(self.task['id'],self.task['version'])['html'].encode()).hexdigest())
        self.assertEqual(self.task['ledger']['used']['model_calls'],0)
        self.assertEqual(self.task['ledger']['used']['data_requests'],0)

    def test_safe_single_and_multiple_references_share_one_presentation_contract(self):
        from researchhelper.visual import knowledge_sources
        source={'url':'https://example.org/manual','publisher':'公开来源','sections':['第一章','第二章']}
        self.assertEqual(knowledge_sources({'source_reference':source}),knowledge_sources({'source_reference':[source]}))
        markup=knowledge_sources({'source_reference':[source,copy.deepcopy(source)]})
        self.assertEqual(markup.count('href='),1)
        self.assertIn('第一章',markup);self.assertIn('第二章',markup)

    def test_retained_array_and_current_additional_sources_are_both_preserved(self):
        from researchhelper.visual import knowledge_sources
        for entry in (RETAINED_VERSIONS[('rh_k_etf_measures',1)],self.entry):
            with self.subTest(version=entry['version']):
                markup=knowledge_sources(entry)
                self.assertEqual(markup.count('href='),2)
                self.assertIn('investor.gov',markup);self.assertIn('finra.org',markup)

    def test_malformed_or_credentialed_links_do_not_become_navigation(self):
        from researchhelper.visual import knowledge_sources
        for source in (None,True,'not-a-reference',{'url':'javascript:alert(1)'},
                       {'url':'data:text/html,x'},{'url':'https://name:PRIVATE@example.org/x'},
                       {'url':'https://example.org/\npath'}, {'url':7}):
            with self.subTest(source=source):
                markup=knowledge_sources({'source':'留存来源说明','source_reference':[source]})
                self.assertNotIn('href=',markup);self.assertNotIn('PRIVATE',markup)
                self.assertIn('留存来源说明',markup)

    def test_unstructured_legacy_source_is_kept_without_guessing_urls(self):
        from researchhelper.visual import knowledge_sources
        text='原规范第七章；网址未登记'
        self.assertEqual(knowledge_sources({'source':text}),text)
        self.assertNotIn('href=',knowledge_sources({'source':text}))

    def test_invalid_sections_are_not_silently_used_as_markup(self):
        from researchhelper.visual import knowledge_sources
        markup=knowledge_sources({'source_reference':{'url':'https://example.org/a',
            'publisher':'<b>来源</b>','sections':['<script>x</script>',None,3]}})
        self.assertIn('&lt;b&gt;来源&lt;/b&gt;',markup)
        self.assertIn('&lt;script&gt;x&lt;/script&gt;',markup)
        self.assertNotIn('<script>',markup)


if __name__=='__main__':unittest.main()
