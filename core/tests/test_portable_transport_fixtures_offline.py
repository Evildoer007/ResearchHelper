"""Keep original transport assertions with explicit test-only CA injection.

Production ignores build-machine certificate paths. Local TLS tests must trust
their generated CA explicitly inside their own workers, without disabling
certificate or hostname checks.
"""
import os
from unittest.mock import patch

from tests import test_isolated_model_transport_offline as original_model
from tests import test_source_transport_offline as original_source
from tests import test_current_source_transport_offline as current_source


model_fixture_worker = original_model.model_fixture_worker


def source_fixture_worker(connection, deadline):
    with patch('researchhelper.https_transport.bundled_ca_file', return_value=os.environ['SSL_CERT_FILE']):
        original_source.fixture_source_worker(connection, deadline)


class PortableModelTransport(original_model.IsolatedModelTransportTests):
    """Keep the portable fixture entry used by the streaming regressions."""


class PortableSupplierTransport(current_source.CurrentSourceTransportTests):
    def setUp(self):
        super().setUp()
        self.collector.worker_target = source_fixture_worker
