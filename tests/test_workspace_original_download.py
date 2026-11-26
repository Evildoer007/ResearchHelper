"""Verify raw file downloads from active research and earlier versions."""
import base64
import hashlib
import json
import tempfile
import unittest

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.archive_codec import REFERENCE_KEY
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent

from tests.test_task_backup_package import backup_document
from tests.test_task_backup_roundtrip import BackupClient, assert_http_status


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_path(task, file):
    return "/api/tasks/" + task["id"] + "/original-file/" + str(file["research_version"]) + "/" + file["id"]


def upload(client, task, name, raw):
    return client.call("/api/tasks/" + task["id"] + "/material", {
        "name": name, "base64": base64.b64encode(raw).decode(), "include_record": True,
    }, view=True)


def prepare_original_downloads(base):
    client = BackupClient(base)
    task = client.call("/api/tasks", {"question": "独立原件下载回放"})
    route = "/api/tasks/" + task["id"]
    large = backup_document(27)
    added = upload(client, task, "有效原件.docx", large)
    pending = b"\xef\xbb\xbf\xff\x00"
    assert_http_status(400, client.call, route + "/material", {
        "name": "解析失败.txt", "base64": base64.b64encode(pending).decode(),
    })
    empty = upload(client, task, "空原件.txt", b"")
    current = client.call(route, view=True)
    assert b"original_base64" not in json.dumps(current).encode()
    by_name = {file["name"]: file for file in current["original_files"]}
    assert by_name["有效原件.docx"]["artifact_id"] == added["material"]["id"]
    assert by_name["解析失败.txt"]["artifact_id"] is None
    assert by_name["空原件.txt"]["artifact_id"] == empty["material"]["id"]
    assert set(by_name) == {"有效原件.docx", "解析失败.txt", "空原件.txt"}
    for name, raw in (("有效原件.docx", large), ("解析失败.txt", pending), ("空原件.txt", b"")):
        file = by_name[name]
        assert file["byte_count"] == len(raw) and file["sha256"] == digest(raw)
        assert client.call(file_path(task, file), raw=True) == raw

    thread = client.call("/api/cases", {"id": "etf"})
    history_route = "/api/tasks/" + thread["id"]
    name = "同名原件.txt"
    old, new = "版本一原文。".encode(), "版本二原文。".encode()
    upload(client, thread, name, old)
    client.call(history_route + "/start", {"expected_execution": client.execution})
    client.saved(history_route, 1)
    client.call(history_route + "/update", {"cutoff": "2026-07-22T18:00:00+08:00", "expected_execution": client.execution})
    completed = client.saved(history_route, 2)
    upload(client, thread, name, new)
    history = client.call(history_route, view=True)
    assert history["delivery"] == completed["delivery"] and history["ledger"] == completed["ledger"]
    pair = {file["research_version"]: file for file in history["original_files"] if file["name"] == name}
    assert set(pair) == {1, 2}
    assert pair[1]["sha256"] == digest(old) and pair[2]["sha256"] == digest(new)
    assert client.call(file_path(thread, pair[1]), raw=True) == old
    assert client.call(file_path(thread, pair[2]), raw=True) == new
    assert_http_status(400, client.call, file_path(thread, dict(pair[1], research_version=2)))
    assert_http_status(400, client.call, file_path(task, pair[1]))
    sales = BackupClient(base, "sales")
    assert_http_status(403, sales.call, file_path(task, by_name["有效原件.docx"]))
    assert_http_status(403, sales.call, file_path(thread, pair[1]))
    assert client.call(route, view=True) == current and client.call(history_route, view=True) == history
    return dict(task=task["id"], artifact=added["material"]["id"], files=by_name, history=thread["id"],
                versions=[pair[1], pair[2]], owner_isolated=True, ledger_unchanged=True)


class WorkspaceOriginalDownloadTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="rh-original-download-")
        self.addCleanup(folder.cleanup)
        self.archive = ResearchArchive(folder.name)
        self.agent = ResearchAgent(self.archive, ROOT, offline=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create("核对本地原件", intent="原件内容核对")

    def test_original_receipt_listing_survives_a_missing_byte_value(self):
        first = self.archive.retain_original(self.task, "material", "失效原件.txt", b"broken receipt " * 2600)
        second = self.archive.retain_original(self.task, "material", "健康原件.txt", b"valid")
        with self.archive.connect() as db:
            body = json.loads(db.execute("SELECT body FROM originals WHERE id=?", (first,)).fetchone()[0])
            db.execute("DELETE FROM archive_values WHERE id=?", (body["original_base64"][REFERENCE_KEY],))
        self.assertEqual([row["id"] for row in self.archive.original_files(self.task)], [first, second])
        self.assertEqual([row["id"] for row in self.archive.retained_originals(self.task, pending_only=True)], [first, second])
        self.assertEqual(self.archive.read_original_file(self.task, 1, second), b"valid")
        with self.assertRaises(ContractError):
            self.archive.read_original_file(self.task, 1, first)

    def test_original_embedded_in_legacy_snapshot_keeps_exact_bytes(self):
        raw = b"\xef\xbb\xbflegacy"
        task = self.agent.add_material(self.task["id"], "旧材料.txt", raw)
        ident = task["materials"][0]["id"]
        with self.archive.connect() as db:
            db.execute("DELETE FROM originals WHERE task_id=?", (task["id"],))
        files = self.archive.original_files(task)
        self.assertEqual([(file["id"], file["sha256"]) for file in files], [(ident, digest(raw))])
        self.assertEqual(self.archive.read_original_file(task, 1, ident), raw)
        backup = self.archive.task_backup(task)
        self.assertEqual([(row["research_version"], base64.b64decode(row["original_base64"]))
                          for row in backup["originals"]], [(1, raw)])
        self.assertEqual(self.archive.original_files(dict(task, id="another")), [])
        with self.assertRaises(ContractError):
            self.archive.read_original_file(task, 2, ident)

    def test_backup_recovers_embedded_files_from_each_version_without_receipt_duplicates(self):
        raw = b"first version"
        task = self.agent.add_material(self.task["id"], "公告.txt", raw)
        with self.archive.connect() as db:
            db.execute("DELETE FROM originals WHERE task_id=?", (task["id"],))
        current = self.archive.save(dict(task, version=2, materials=[]))
        fresh = b"second version"
        current = self.agent.add_material(current["id"], "公告.txt", fresh)
        backup = self.archive.task_backup(current)
        files = [(row["research_version"], row["name"], base64.b64decode(row["original_base64"]))
                 for row in backup["originals"]]
        self.assertEqual(files, [(1, "公告.txt", raw), (2, "公告.txt", fresh)])
        self.assertEqual(self.archive.get(current["id"]), current)
