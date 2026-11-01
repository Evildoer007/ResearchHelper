"""Keep original transport assertions with explicit test-only CA injection.

Production ignores build-machine certificate paths. Local TLS tests must trust
their generated CA explicitly inside their own workers, without disabling
certificate or hostname checks. Original test files remain unchanged.
"""
import os
from unittest.mock import patch

from researchhelper.contracts import ContractError
from researchhelper.model import _model_http_worker
from tests import test_isolated_model_transport_offline as original_model
from tests import test_source_transport_offline as original_source
from tests import test_current_source_transport_offline as current_source


def model_fixture_worker(connection, deadline):
    with patch('researchhelper.https_transport.bundled_ca_file', return_value=os.environ['SSL_CERT_FILE']):
        _model_http_worker(connection, deadline)


def source_fixture_worker(connection, deadline):
    with patch('researchhelper.https_transport.bundled_ca_file', return_value=os.environ['SSL_CERT_FILE']):
        original_source.fixture_source_worker(connection, deadline)


class PortableModelTransport(original_model.IsolatedModelTransportTests):
    def setUp(self):
        super().setUp()
        self.service.worker_target = model_fixture_worker

    def test_offline_and_missing_configuration_start_no_worker(self):
        # Clearing a configured origin is now explicit, rather than a blank
        # key silently moving to another endpoint. All original checks remain.
        for offline, empty in ((True, False), (False, True)):
            self.service.offline = offline
            if empty:
                self.settings.save({'endpoint': '', 'model': '', 'api_key': '', 'clear_key': True})
            task = self.task()
            with patch('researchhelper.model.multiprocessing.get_context') as context:
                with self.assertRaises(ContractError):
                    self.service.reason(task, [])
                context.assert_not_called()
            self.assertEqual(task['ledger']['used']['model_calls'], 0)


class PortableSupplierTransport(current_source.CurrentSourceTransportTests):
    def setUp(self):
        super().setUp()
        self.collector.worker_target = source_fixture_worker
