"""Original auth assertions with the currently required quote request scope.

Old fixtures omit cutoff and use an unregistered Fill setting. Do not weaken
production preflight or overwrite the original tests to exercise authentication.
"""
from tests import test_ifind_auth_resolution_offline as original


class CurrentIFindAuthContractTests(original.IfindAuthResolutionTests):
    def setUp(self):
        super().setUp()
        self.task.update(objects=['600000.SH'], question='核对600000.SH', cutoff='2026-10-02T18:00:00+08:00')

    def acquire(self, credentials=None):
        params = dict(original.PARAMS, functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
        return self.collector.external(self.task, 'ifind', 'cmd_history_quotation', params,
            credentials or dict(refresh_token=original.REFRESH))
