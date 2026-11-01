"""Keep original transport assertions with the current request grammar.

The old fixture omits declared scope/fields/window and now fails preflight
before reaching transport. Original tests remain untouched and are not called
passed; this independent derived suite changes fixture inputs only.
"""
from tests import test_source_transport_offline as original


class CurrentSourceTransportTests(original.SourceTransportTests):
    def task(self):
        task = super().task()
        task['objects'] = ['600000.SH', '510300.SH']
        task['question'] = '600000.SH及510300.SH测试取数'
        return task

    def ifind(self, task, credentials=None):
        return self.collector.external(task, 'ifind', 'cmd_history_quotation',
            {'codes': '600000.SH', 'indicators': 'close', 'startdate': '2026-09-29',
                'enddate': '2026-09-30', 'functionpara': {'Currency': 'RMB', 'CPS': '1', 'Fill': 'Omit'}},
            credentials or {'access_token': original.SECRET})
