"""New source-phase build entry; the earlier release builder stays untouched."""
from unittest.mock import patch

from tests import build_report_reader_candidate as retained
from tests.harness_release_gate import current_gate


def build():
    # The replacement gate is stricter and records its own OS-isolated receipt.
    # No old status is forged and no deployment snapshot is treated as passed.
    with patch.object(retained, 'current_gate', current_gate):
        return retained.build()


if __name__ == '__main__':
    build()
