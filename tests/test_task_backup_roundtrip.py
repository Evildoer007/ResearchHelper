"""Exercise full local backup files and passive imports through the HTTP boundary."""
import base64
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from tests import ROOT
from researchhelper.service import make_server


class BackupClient:
    def __init__(self, base, account="admin"):
        self.base = base.rstrip("/")
        self.cookie = ""
        self.http = build_opener(ProxyHandler({}))
        self.token = self.call("/api/auth/status")["token"]
        self.call("/api/auth/login", {"account": account, "password": {"admin": "66666666", "sales": "88888888"}[account]})
        settings = self.call("/api/bootstrap")["execution"]
        self.execution = {key: settings[key] for key in ("mode", "policy_version")}
        assert self.execution["mode"] == "offline"

    def call(self, path, payload=None, *, raw=False, view=False):
        headers = {"Cookie": self.cookie}
        if view:
            headers["X-ResearchHelper-View"] = "workspace-v1"
        if payload is not None:
            headers.update({"X-ResearchHelper-Token": self.token, "Content-Type": "application/json"})
        body = json.dumps(payload).encode() if isinstance(payload, dict) else payload
        with self.http.open(Request(self.base + path, data=body, headers=headers), timeout=25) as response:
            cookie = response.headers.get("Set-Cookie")
            if cookie:
                self.cookie = cookie.split(";", 1)[0]
            result = response.read()
        return result if raw else json.loads(result)

    def saved(self, path, version):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            task = self.call(path)
            if task.get("delivery", None) and task["delivery"]["version"] == version:
                assert task["ledger"]["used"]["model_calls"] == task["ledger"]["used"]["data_requests"] == 0
                return task
            if task["status"] != "运行中":
                break
            time.sleep(.04)
        raise AssertionError("离线报告未交付")


def assert_http_status(code, call, *args):
    try:
        call(*args)
    except HTTPError as error:
        assert error.code == code, error
        error.close()
    else:
        raise AssertionError(f"Expected HTTP {code}")


def exercise_backup(base):
    client = BackupClient(base)
    task = client.call("/api/cases", {"id": "etf"})
    route = "/api/tasks/" + task["id"]
    original = b"Source text for the offline backup check.\n"
    client.call(route + "/material", {"name": "source.txt", "base64": base64.b64encode(original).decode()})
    client.call(route + "/start", {"expected_execution": client.execution})
    client.saved(route, 1)
    client.call(route + "/update", {"cutoff": "2026-07-22T18:00:00+08:00", "expected_execution": client.execution})
    current = client.saved(route, 2)
    backup = client.call(route + "/backup", raw=True)
    decoded = json.loads(backup)
    assert decoded["tasks"] == [current]
    assert [item["version"] for item in decoded["reports"]] == [1, 2]
    for edition in decoded["reports"]:
        assert edition["html"].encode() == client.call(route + "/report/" + str(edition["version"]), raw=True)
        if "html_sha256" in edition:  # Also replay the prior candidate's backup.
            assert edition["html_sha256"] == hashlib.sha256(edition["html"].encode()).hexdigest()
        assert edition["content"]["research_version"] == edition["version"]
    retained = decoded["originals"][0]
    assert len(decoded["originals"]) == 1
    assert retained["research_version"] == 1 and retained["task_id"] == task["id"]
    assert retained["sha256"] == hashlib.sha256(original).hexdigest()
    assert base64.b64decode(retained["original_base64"], validate=True) == original

    archive = client.call("/api/import?file=1", backup)
    archive_route = "/api/tasks/" + archive["id"]
    assert archive["id"] != task["id"] and archive["question"] == current["question"]
    assert archive["parent"] == "legacy-import" and archive["batch"] == 0
    assert archive["owner_account"] == "admin" and archive["revision"] == 1
    assert not archive.get("delivery") and not archive["evidence"] and not archive["materials"]
    assert "legacy_backup_text" not in archive and "legacy_backup_text" not in client.call(archive_route, view=True)
    assert client.call(archive_route + "/backup", raw=True) == backup
    assert_http_status(400, client.call, archive_route + "/start", {"expected_execution": client.execution})
    assert_http_status(400, client.call, archive_route + "/report")
    sales = BackupClient(base, "sales")
    assert_http_status(403, sales.call, archive_route + "/backup")
    assert_http_status(403, sales.call, route + "/backup")
    assert client.call(route) == current
    return {"report_versions": [1, 2], "backup_bytes": len(backup), "backup_sha256": hashlib.sha256(backup).hexdigest(),
            "original_sha256": retained["sha256"], "import_replays_identical_bytes": True, "owner_isolated": True}


def exercise_large_backup(base):
    client = BackupClient(base)
    task = client.call("/api/tasks", {"question": "原件归档检查"})
    route = "/api/tasks/" + task["id"]
    original = b"reference-012345678901234567890123456789\n" * 42500
    client.call(route + "/material", {"name": "source.txt", "base64": base64.b64encode(original).decode()})
    backup = client.call(route + "/backup", raw=True)
    assert len(backup) > 2_000_000
    assert base64.b64decode(json.loads(backup)["originals"][0]["original_base64"], validate=True) == original
    archived = client.call("/api/import?file=1", backup)
    assert client.call("/api/tasks/" + archived["id"] + "/backup", raw=True) == backup
    return {"large_file_bytes": len(backup), "sha256": hashlib.sha256(backup).hexdigest()}


def exercise_legacy_package_index(base, archive):
    from tests.test_task_backup_package import fixture, package_bytes, sha256

    client = BackupClient(base)
    backup, registered = fixture()
    task = backup["tasks"][0]
    raw = b"\xef\xbb\xbfembedded archived file\r\n"
    task["materials"].append(dict(task_id=task["id"], research_version=2, kind="material", snapshot=dict(
        name="留存的旧公告.txt", hash=sha256(raw), original_base64=base64.b64encode(raw).decode("ascii"))))
    wire = package_bytes(backup)
    imported = client.call("/api/import?file=zip", wire)
    saved = archive.get(imported["id"])
    index = saved["backup_package"]
    assert index["files"][-1]["filename"] == "留存的旧公告.txt"
    # Simulate an archive imported before embedded files had an index.
    older = dict(index, files=index["files"][:-1])
    older.pop("file_index_version")
    saved = archive.save(dict(saved, backup_package=older))
    route = "/api/tasks/" + saved["id"]
    assert client.call(route, view=True)["backup_package"] == older
    files = client.call(route + "/backup-files")["files"]
    assert files == index["files"]
    assert client.call(route + "/backup-file/1", raw=True) == registered
    assert client.call(route + "/backup-file/2", raw=True) == raw
    assert_http_status(400, client.call, route + "/backup-file/3")
    assert client.call(route + "/backup-package", raw=True) == wire
    other = BackupClient(base, "sales")
    assert_http_status(403, other.call, route + "/backup-files")
    assert_http_status(403, other.call, route + "/backup-file/2")
    assert archive.get(saved["id"]) == saved
    return dict(archive_id=saved["id"], filename="留存的旧公告.txt", version=2, sha256=sha256(raw),
                bytes=len(raw), source_backup_sha256=sha256(wire), read_only=True, account_isolated=True)


class TaskBackupRoundtripTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="rh-task-backup-test-")
        self.addCleanup(directory.cleanup)
        server = make_server(Path(directory.name), ROOT, authentication=True, preset_accounts=True)
        self.server = server
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = "http://127.0.0.1:" + str(server.server_port)

    def test_reports_originals_and_account_scope(self):
        exercise_backup(self.base)

    def test_own_backup_above_the_previous_import_limit(self):
        exercise_large_backup(self.base)

    def test_already_imported_zip_recovers_missing_originals_read_only(self):
        archive = self.server.agent.archive
        case = exercise_legacy_package_index(self.base, archive)
        task_id = case["archive_id"]
        with archive.connect() as db:
            payload, = db.execute("SELECT payload FROM backup_packages WHERE task_id=?", (task_id,)).fetchone()
            db.execute("UPDATE backup_packages SET payload=? WHERE task_id=?",
                       (payload[:-1] + bytes([payload[-1] ^ 1]), task_id))
        client = BackupClient(self.base)
        route = "/api/tasks/" + task_id
        assert_http_status(400, client.call, route + "/backup-files")
        assert_http_status(400, client.call, route + "/backup-file/2")

    def test_invalid_zip_does_not_create_an_archive(self):
        client = BackupClient(self.base)
        before = client.call("/api/bootstrap")["tasks"]
        assert_http_status(400, client.call, "/api/import?file=zip", b"PK\x03\x04invalid backup")
        self.assertEqual(client.call("/api/bootstrap")["tasks"], before)

    def test_legacy_wire_precision_and_invalid_input(self):
        client = BackupClient(self.base)
        wire = '\ufeff{ "title": "历史备份", "data": 9007199254740995, "reviewed": true }\n'.encode()
        archived = client.call("/api/import?file=1", wire)
        self.assertEqual(archived["question"], "历史备份")
        self.assertEqual(client.call("/api/tasks/" + archived["id"] + "/backup", raw=True), wire)
        legacy = client.call("/api/import", {"backup": [{"old": True}]})
        self.assertEqual(client.call("/api/tasks/" + legacy["id"] + "/backup"), [{"old": True}])
        before = client.call("/api/bootstrap")["tasks"]
        invalid = (b"null", b"{", b'{"x":1,"x":2}', b'{"x":1e999}', "{}".encode("utf-16"))
        for body in invalid:
            assert_http_status(400, client.call, "/api/import?file=1", body)
        with patch.object(self.server.agent.archive, "save", side_effect=OSError("controlled failure")):
            assert_http_status(500, client.call, "/api/import?file=1", '{"title":"未归档"}'.encode())
        self.assertEqual(client.call("/api/bootstrap")["tasks"], before)
