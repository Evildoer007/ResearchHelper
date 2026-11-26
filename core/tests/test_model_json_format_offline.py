"""JSON prompt syntax and untouched response rejection, independent checks."""
import json
import unittest

from researchhelper.contracts import ContractError
from researchhelper.reasoning import PHASE_FIELDS, PROMPT_FORMAT_VERSION, model_object
from tests import test_document_content_offline as documents


class CapturingModel:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def reason_phase(self, task, messages, phase):
        self.calls.append((task['id'], messages, phase))
        return self.output


class ModelJSONFormatTests(unittest.TestCase):
    def setUp(self):
        self.fixture = documents.DocumentContentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = self.fixture.material_task()

    def test_every_phase_has_parseable_example_with_exact_fields(self):
        for phase, fields in PHASE_FIELDS.items():
            with self.subTest(phase=phase):
                output = {field: {} if field == 'answers' else '' if field in {'reason', 'expected_change', 'stop_reason'} else [] for field in fields}
                model = CapturingModel(json.dumps(output))
                value, receipt = model_object(model, self.task, phase, {}, fields)
                system = model.calls[0][1][0]['content']
                example = system.split('JSON格式样例，仅说明结构，不代表本任务没有证据：', 1)[1].split('。请输出合法json：', 1)[0]
                self.assertEqual(json.loads(example), output)
                self.assertEqual(value, output)
                self.assertEqual(receipt['prompt_format_version'], PROMPT_FORMAT_VERSION)
                escaped = system.split('字符串转义样例，仅说明序列化，不是原件内容：', 1)[1].split('。转义后的字符串', 1)[0]
                self.assertEqual(json.loads(escaped)['text'], '引号"、反斜杠\\及换行\n')
                self.assertEqual(len(model.calls), 1)

    def test_original_quotes_backslashes_and_linebreaks_survive_parsing(self):
        original = '原文包含"条款"、路径\\segment\n下一行。'
        model = CapturingModel(json.dumps({'evidence_specs': [{'text': original}]}, ensure_ascii=False))
        value, _ = model_object(model, self.task, 'extract', {}, {'evidence_specs'})
        self.assertEqual(value['evidence_specs'][0]['text'], original)
        # model_object parses shape only; it does not sign evidence approval.
        self.assertEqual(self.task['evidence'], [])

    def test_invalid_json_is_not_repaired_or_retried(self):
        for output in ('{"evidence_specs":[{"text":"原文"引号""}]}',
                       '{"evidence_specs":[],}', '```json\n{"evidence_specs":[]}\n```'):
            with self.subTest(output=output):
                model = CapturingModel(output)
                with self.assertRaises(ContractError):
                    model_object(model, self.task, 'extract', {}, {'evidence_specs'})
                self.assertEqual(len(model.calls), 1)
                self.assertEqual(model.output, output)
                self.assertEqual(self.task['evidence'], [])


if __name__ == '__main__':
    unittest.main()
