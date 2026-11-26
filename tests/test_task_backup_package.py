"""Round-trip a packed checkpoint and verify the actual archived file bytes."""
import base64
import hashlib
import io
import json
import random
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.backup_package import (LITERAL, MANIFEST, REF, inspect_package, read_package_file,
                                         read_saved_package_file_index, write_package)
from researchhelper.contracts import ContractError

from tests.test_task_backup_roundtrip import BackupClient, assert_http_status


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def backup_document(seed):
    from docx import Document

    file = io.BytesIO()
    document = Document()
    document.add_paragraph(f"Research backup size fixture {seed}.")
    document.save(file)
    with ZipFile(file, "a", compression=ZIP_STORED) as archive:
        # Keep the attachment almost at the supported per-file size. The
        # random bytes do not shrink to a misleadingly small backup.
        archive.writestr("word/media/fixture.bin", random.Random(seed).randbytes(24 * 1024 * 1024))
    return file.getvalue()


def prepare_package_case(base):
    client = BackupClient(base)
    task = client.call("/api/cases", {"id": "etf"})
    route = "/api/tasks/" + task["id"]
    client.call(route + "/start", {"expected_execution": client.execution})
    completed = client.saved(route, 1)
    html = client.call(route + "/report/1", raw=True)
    originals = []
    for index, name in enumerate(("市场资料一.docx", "市场资料二.docx"), 1):
        document = backup_document(index)
        current = client.call(route + "/material",
                              {"name": name, "base64": base64.b64encode(document).decode(), "include_record": True}, view=True)
        assert current["material"]["hash"] == sha256(document)
        workbench = current["task"]
        row = next(item for item in workbench["materials"] + workbench.get("pending_materials", [])
                   if item["id"] == current["material"]["id"])
        assert row["status"] == "已解析" and f"Research backup size fixture {index}." in row["parts"][0]["text"]
        originals.append(dict(filename=name, byte_count=len(document), sha256=current["material"]["hash"]))
    task_view = client.call(route, view=True)
    assert task_view["delivery"] == completed["delivery"] and task_view["ledger"]["used"] == completed["ledger"]["used"]
    view = json.dumps(task_view).encode()
    assert b"original_base64" not in view and len(view) < 1_000_000
    try:
        client.call(route + "/backup", raw=True)
    except HTTPError as error:
        assert error.code == 400 and json.loads(error.read())["failure_code"] == "backup_package_required"
    else:
        raise AssertionError("Expected ZIP fallback for a 48MiB task")
    return dict(task_id=task["id"], source_revision=task_view["revision"], source_version=task_view["version"],
                original_files=originals, report=dict(sha256=sha256(html), byte_count=len(html), version=1),
                original_bytes=sum(item["byte_count"] for item in originals), workspace_bytes=len(view))


def verify_saved_package(base, case, archive_id, download_path):
    client = BackupClient(base)
    route = "/api/tasks/" + archive_id
    saved = client.call(route, view=True)
    assert saved["parent"] == "legacy-import" and saved["owner_account"] == "admin"
    assert not saved["evidence"] and not saved.get("delivery") and not saved["batch"]
    assert saved["ledger"]["used"]["model_calls"] == saved["ledger"]["used"]["data_requests"] == 0
    wire = Path(download_path).read_bytes()
    assert len(wire) > 45 * 1024 * 1024 and len(wire) < case["original_bytes"] + 2 * 1024 * 1024
    receipt, content = inspect_package(wire, expand=True)
    assert receipt == saved["backup_package"]
    assert content["tasks"][0]["id"] == case["task_id"] and content["tasks"][0]["version"] == case["source_version"]
    assert content["reports"][0]["html_sha256"] == case["report"]["sha256"]
    for expected, item in zip(case["original_files"], content["originals"], strict=True):
        material = base64.b64decode(item["original_base64"], validate=True)
        assert item["name"] == expected["filename"] and sha256(material) == expected["sha256"]
    assert client.call(route + "/backup-package", raw=True) == wire
    assert_http_status(400, client.call, route + "/start", {"expected_execution": client.execution})
    other = BackupClient(base, "sales")
    assert_http_status(403, other.call, route + "/backup-package")
    assert_http_status(403, other.call, route + "/backup-file/0")
    return dict(archive_id=archive_id, package_bytes=len(wire), sha256=receipt["sha256"],
                original_bytes=case["original_bytes"], workspace_bytes=case["workspace_bytes"], owner_isolated=True)


def fixture():
    original = random.Random(17).randbytes(18000)
    raw = base64.b64encode(original).decode()
    text = "复用完整数值记录" * 2500
    html = "<html><p>可读研究结论</p></html>"
    task = dict(id="rh_case", version=2, question="保留原值", revision=3, score=9007199254740999,
                materials=[{"original_base64": raw, "snapshot": {"original_base64": raw, "text": text}}],
                notes=[text, {REF: "literal metadata", LITERAL: "retained"}])
    report = dict(version=1, content=dict(task_id=task["id"], research_version=1), html=html,
                  html_sha256=sha256(html.encode()))
    entry = dict(task_id=task["id"], research_version=1, name="../COM1.docx", original_base64=raw,
                 byte_count=len(original), sha256=sha256(original))
    return {"workspaceVersion": 4, "tasks": [task], "reports": [report], "originals": [entry]}, original


def package_bytes(backup):
    stream = io.BytesIO()
    length = write_package(backup, stream)
    wire = stream.getvalue()
    assert length == len(wire)
    return wire


class TaskBackupPackageTests(TestCase):
    def test_saved_package_index_reads_no_unrelated_member_payloads(self):
        backup, original = fixture()
        wire = package_bytes(backup)
        expected = inspect_package(wire)["files"]
        opened = []
        original_open = ZipFile.open

        def record_open(archive, name, *args, **kwargs):
            opened.append(name.filename if isinstance(name, ZipInfo) else name)
            return original_open(archive, name, *args, **kwargs)

        with patch.object(ZipFile, "open", record_open):
            self.assertEqual(read_saved_package_file_index(wire), expected)
            self.assertEqual(opened, [MANIFEST])
            self.assertEqual(read_package_file(wire, expected[1]), original)
            self.assertEqual(opened, [MANIFEST, expected[1]["path"]])

    def test_old_package_recovers_embedded_inline_and_externalized_originals(self):
        backup, existing = fixture()
        task = backup["tasks"][0]
        task["version"] = 2
        short = b"\xef\xbb\xbfraw"
        large = b"legacy material\n" * 800

        def record(raw, name, version=1, **overrides):
            material = dict(name=name, hash=sha256(raw), original_base64=base64.b64encode(raw).decode("ascii"))
            return dict(dict(task_id=task["id"], research_version=version, kind="material", snapshot=material), **overrides)

        listed = backup["originals"][0]
        task["materials"] = [record(existing, listed["name"]),
                             record(existing, listed["name"], version=2, retained_from_version=1),
                             record(short, "旧公告.txt"),
                             record(short, "错误主体.txt", task_id="other"),
                             record(short, "错误版本.txt", version=True)]
        task["source_records"] = [record(large, "旧公告.txt", version=2)]
        task["source_records"][0]["kind"] = "filing"
        task["source_records"][0]["snapshot"] = {"material": task["source_records"][0]["snapshot"]}
        damaged = record(short, "损坏.txt")
        damaged["snapshot"]["hash"] = sha256(b"different")
        task["source_records"].append(damaged)
        wire = package_bytes(backup)
        receipt, restored = inspect_package(wire, expand=True)
        self.assertEqual(restored, backup)
        self.assertEqual(receipt, inspect_package(wire))
        originals = [row for row in receipt["files"] if row["kind"] == "original"]
        self.assertEqual([(row["version"], row["filename"]) for row in originals],
                         [(1, "_COM1.docx"), (1, "旧公告.txt"), (2, "旧公告.txt")])
        self.assertEqual([read_package_file(wire, row) for row in originals], [existing, short, large])

    def test_pack_restores_full_checkpoint_and_deduplicates_originals(self):
        backup, original = fixture()
        wire = package_bytes(backup)
        receipt, restored = inspect_package(wire, expand=True)
        self.assertEqual(restored, backup)
        self.assertEqual(receipt["title"], backup["tasks"][0]["question"])
        self.assertEqual([item["kind"] for item in receipt["files"]], ["report", "original"])
        self.assertEqual(read_package_file(wire, receipt["files"][1]), original)
        with ZipFile(io.BytesIO(wire)) as archive:
            manifest = json.loads(archive.read(MANIFEST))
            self.assertEqual(set(manifest["objects"]), {sha256(original), backup["reports"][0]["html_sha256"],
                                                        sha256(backup["tasks"][0]["notes"][0].encode())})
            self.assertTrue(all(".." not in name for name in archive.namelist()))

    def test_rejects_tampering_unbound_members_and_symlinks(self):
        backup, _ = fixture()
        source = package_bytes(backup)
        for damage in ("hash", "unbound", "symlink", "scope"):
            with self.subTest(damage=damage):
                output = io.BytesIO()
                with ZipFile(io.BytesIO(source)) as original, ZipFile(output, "w") as changed:
                    for name in original.namelist():
                        raw = original.read(name)
                        if damage == "hash" and name.startswith("originals/"):
                            raw = raw[:-1] + bytes((raw[-1] ^ 1,))
                        if damage == "scope" and name == MANIFEST:
                            document = json.loads(raw)
                            document["backup"]["originals"][0]["task_id"] = "other"
                            raw = json.dumps(document).encode()
                        if damage == "symlink" and name.startswith("originals/"):
                            info = ZipInfo(name)
                            info.create_system = 3
                            info.external_attr = 0o120777 << 16
                            name = info
                        changed.writestr(name, raw)
                    if damage == "unbound":
                        changed.writestr("data/" + "a" * 64, b"unknown")
                with self.assertRaises(ContractError):
                    inspect_package(output.getvalue())

    def test_archive_keeps_package_across_revision_and_failed_insert(self):
        backup, _ = fixture()
        wire = package_bytes(backup)
        receipt = inspect_package(wire)
        with TemporaryDirectory() as directory:
            archive = ResearchArchive(directory)
            task = dict(id="rh_archive", version=1, revision=0, backup_package=receipt, parent="legacy-import")
            archive.save(task, backup_package=wire)
            archive.save(task)
            self.assertEqual(archive.backup_package(archive.get(task["id"])), wire)
            with self.assertRaises(sqlite3.IntegrityError):
                archive.save(task, backup_package=wire)
            self.assertEqual(archive.get(task["id"])["revision"], 2)
