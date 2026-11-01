"""JSON copying preserves values, detachment and original archive safeguards."""
import copy
import unittest

from tests import archive_value_storage_fixture as storage_fixture
from researchhelper.contracts import canonical, copy_json_value as clone_json_tree


class JsonValueCopyTests(unittest.TestCase):
    def test_every_json_type_and_literal_key_is_unchanged(self):
        original=dict(记录=[None,False,True,0,-0.0,1,1.0,'中文及引号"与反斜线\\',[],{}],
                      snapshot={'_researchhelper_archive_value':'普通文字，不能执行引用',
                                'rows':[{'number':i,'unit':'%','nullable':None} for i in range(300)]})
        result=clone_json_tree(original)
        self.assertEqual(canonical(result),canonical(copy.deepcopy(original)))
        self.assertIsNot(result['snapshot']['rows'][0],original['snapshot']['rows'][0])
        result['snapshot']['rows'][0]['number']=99
        self.assertEqual(original['snapshot']['rows'][0]['number'],0)

    def test_large_immutable_strings_can_share_without_mutable_container_alias(self):
        text='大型原件正文不应每次复制同一个不可变字符串。'*10000
        original={'rows':[{'text':text,'field':['原始口径']} for _ in range(3)]}
        result=clone_json_tree(original)
        self.assertIs(result['rows'][0]['text'],text)
        self.assertIsNot(result['rows'][0]['field'],result['rows'][1]['field'])
        result['rows'][0]['field'].append('局部修改')
        self.assertEqual(result['rows'][1]['field'],['原始口径'])
        self.assertEqual(original['rows'][0]['field'],['原始口径'])

    def test_non_json_objects_are_not_converted_into_business_values(self):
        for value in (b'bytes',object(),{1,2}):
            with self.subTest(kind=type(value)),self.assertRaises((TypeError,ValueError)):
                clone_json_tree(value)


class JsonCopyArchiveSafeguards(storage_fixture.ArchiveValueStorageTests):
    """Established safeguards against the actual optimized codec."""


if __name__=='__main__':unittest.main()
