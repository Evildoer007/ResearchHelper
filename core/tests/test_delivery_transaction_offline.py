"""Independent transaction/snapshot tests using isolated SQLite connections."""
import json
import sqlite3
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from tests import test_delivery_integrity_offline as fixture


class DeliveryTransactionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.DeliveryIntegrityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.archive = self.fixture.archive

    def test_external_writer_cannot_change_task_after_freeze_validation(self):
        connect = self.archive.connect
        owner = self
        blocked = []

        class Cursor:
            def __init__(self, cursor):
                self.cursor = cursor

            def fetchone(self):
                row = self.cursor.fetchone()
                with sqlite3.connect(owner.archive.path, timeout=0) as writer:
                    try:
                        writer.execute('UPDATE tasks SET revision=revision+1 WHERE id=?',
                                       (owner.fixture.task['id'],))
                    except sqlite3.OperationalError as error:
                        blocked.append('locked' in str(error))
                    else:
                        blocked.append(False)
                return row

        class Connection:
            def __init__(self):
                self.connection = connect()

            def __enter__(self):
                self.connection.__enter__()
                return self

            def __exit__(self, *args):
                return self.connection.__exit__(*args)

            def execute(self, statement, parameters=()):
                cursor = self.connection.execute(statement, parameters)
                return Cursor(cursor) if statement.startswith('SELECT revision,body FROM tasks') else cursor

        with patch.object(self.archive, 'connect', side_effect=Connection):
            done = self.fixture.deliver()
        self.assertEqual(blocked, [True])
        self.assertEqual(done['status'], '已完成')

    def test_content_array_is_a_controlled_contract_error(self):
        self.fixture.deliver()
        self.fixture.corrupt('content', '[]')
        with self.assertRaises(ContractError):
            self.fixture.report()

    def test_frozen_version_array_is_a_controlled_contract_error(self):
        self.fixture.deliver()
        with self.archive.connect() as database:
            database.execute('UPDATE versions SET body=? WHERE task_id=? AND version=1',
                             ('[]', self.fixture.task['id']))
        with self.assertRaises(ContractError):
            self.fixture.report()

    def test_delivery_scalar_is_a_controlled_contract_error(self):
        self.fixture.deliver()
        with self.archive.connect() as database:
            row = database.execute('SELECT body FROM versions WHERE task_id=? AND version=1',
                                   (self.fixture.task['id'],)).fetchone()
            frozen = json.loads(row[0])
            frozen['delivery'] = 'invalid'
            database.execute('UPDATE versions SET body=? WHERE task_id=? AND version=1',
                             (json.dumps(frozen), self.fixture.task['id']))
        with self.assertRaises(ContractError):
            self.fixture.report()

    def test_report_rows_are_read_in_one_snapshot(self):
        self.fixture.deliver()
        trace = []
        connect = self.archive.connect

        def observed():
            database = connect()
            database.set_trace_callback(trace.append)
            return database

        with patch.object(self.archive, 'connect', side_effect=observed):
            self.fixture.report()
        reads = [index for index, statement in enumerate(trace) if statement.startswith('SELECT')]
        begin = next((index for index, statement in enumerate(trace) if statement == 'BEGIN'), None)
        self.assertIsNotNone(begin)
        self.assertLess(begin, min(reads))
