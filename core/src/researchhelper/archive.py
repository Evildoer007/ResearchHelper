"""Transactional, versioned local archive; installation and business stores stay separate."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import threading
from html import unescape
from pathlib import Path

from .contracts import ContractError, canonical, fingerprint, now
from .archive_codec import encode_body, decode_body, snapshot_decoder
from .backup_package import ORIGINAL_LIMIT, embedded_materials, embedded_original_bytes
from .parallel_study import POLICY as PARALLEL_STUDY_POLICY
from .workspace_views import (SUMMARY_VERSION, valid_cached_summary, valid_workspace_draft_write,
                              validate_workspace_draft, workspace_task_summary)

ORIGINAL_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".html", ".htm"}
ORIGINAL_METADATA = ("id", "task_id", "research_version", "kind", "name", "sha256", "byte_count", "retained_at")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
REPORT_HEAD_BYTES = 8192


class ArchiveRecordError(ContractError):
    """An individual task cannot be decoded or bound to its archive row."""


class WorkspaceDraftConflict(ContractError):
    """Another workspace saved first; the newer durable draft is untouched."""


def _read_task(db, body, ident, *, revision=None, version=None, decode=None):
    try:
        task = decode(body)[0] if decode is not None else decode_body(db, body)
    except ContractError as error:
        raise ArchiveRecordError(str(error)) from None
    except (TypeError, ValueError, KeyError):
        raise ArchiveRecordError("研究档案格式无效，无法读取该任务") from None
    if (not isinstance(task, dict) or task.get('id') != ident
            or revision is not None and (type(task.get('revision')) is not int or task['revision'] != revision)
            or version is not None and (type(task.get('version')) is not int or task['version'] != version)):
        raise ArchiveRecordError("研究档案与任务身份不一致，无法读取该任务")
    if task.get('status') == '运行中' and (
            not isinstance(task.get('nodes'), list) or any(not isinstance(node, dict) for node in task['nodes'])):
        raise ArchiveRecordError("研究检查点格式无效，无法读取该任务")
    return task


class ResearchArchive:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "researchhelper.sqlite3"
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(task_id TEXT, version INTEGER, body TEXT NOT NULL,
                    PRIMARY KEY(task_id,version));
                CREATE TABLE IF NOT EXISTS deliveries(task_id TEXT, version INTEGER, content TEXT NOT NULL,
                    html TEXT NOT NULL, digest TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(task_id,version));
                CREATE TABLE IF NOT EXISTS delivery_titles(task_id TEXT NOT NULL, version INTEGER NOT NULL,
                    digest TEXT NOT NULL, title TEXT NOT NULL, PRIMARY KEY(task_id,version),
                    FOREIGN KEY(task_id,version) REFERENCES deliveries(task_id,version) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS corrections(task_id TEXT, version INTEGER, reason TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS originals(id TEXT PRIMARY KEY, task_id TEXT NOT NULL, version INTEGER NOT NULL,
                    body TEXT NOT NULL, artifact_id TEXT, FOREIGN KEY(task_id) REFERENCES tasks(id));
                CREATE TABLE IF NOT EXISTS task_presentations(owner_account TEXT NOT NULL, thread_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(owner_account,thread_id));
                CREATE TABLE IF NOT EXISTS workspace_drafts(owner_account TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                    write_id TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS archive_values(id TEXT PRIMARY KEY, byte_count INTEGER NOT NULL,
                    compressed BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS backup_packages(task_id TEXT PRIMARY KEY, payload BLOB NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS workspace_summaries(task_id TEXT PRIMARY KEY,
                    task_revision INTEGER NOT NULL, body_sha256 TEXT NOT NULL,
                    view_version INTEGER NOT NULL, summary TEXT NOT NULL, summary_sha256 TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE);
            """)
        os.chmod(self.path, 0o600)

    def connect(self):
        storage_files = (self.path, *(self.path.with_name(self.path.name + suffix)
                                      for suffix in ('-journal', '-wal', '-shm')))
        try:
            redirected = self.root.resolve() != self.root or any(path.is_symlink() for path in storage_files)
        except (OSError, RuntimeError):
            raise ContractError('无法定位研究数据库，请检查存储目录') from None
        if redirected:
            raise ContractError('研究数据库路径包含重定向链接，请检查存储目录')
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def save(self, task, *, backup_package=None):
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT revision FROM tasks WHERE id=?", (task["id"],)).fetchone()
            revision = row[0] if row else 0
            if task.get("revision", 0) != revision:
                raise ContractError("任务已更新，请重新读取，不能覆盖较新版本")
            saved = dict(task, revision=revision + 1, updated_at=now())
            body = encode_body(db, saved)
            db.execute("INSERT INTO tasks VALUES(?,?,?) ON CONFLICT(id) DO UPDATE "
                       "SET revision=excluded.revision,body=excluded.body", (task["id"], revision + 1, body))
            if backup_package is not None:
                db.execute("INSERT INTO backup_packages VALUES(?,?)", (task["id"], backup_package))
            self._store_workspace_summary(db, saved, body)
            frozen = db.execute("SELECT 1 FROM deliveries WHERE task_id=? AND version=?", (task["id"], task["version"])).fetchone()
            if not frozen:
                db.execute("INSERT OR REPLACE INTO versions VALUES(?,?,?)", (task["id"], task["version"], body))
            # Adoption and task persistence commit together. A crash before this
            # transaction leaves the original receipt pending, never lost.
            for record in task.get("materials", []) + task.get("pending_materials", []) + task.get("source_records", []) + task.get("pending_source_records", []):
                if record.get("task_id") != task["id"] or record.get("research_version") != task["version"]:
                    continue
                kind = record.get("kind")
                material = record if kind == "material" else record.get("snapshot", {}).get("material", {}) if kind == "filing" else {}
                if material.get("name") and material.get("hash"):
                    identity = self._original_id(task, kind, material["name"], material["hash"], record.get("directory"))
                    db.execute("UPDATE originals SET artifact_id=? WHERE id=?", (record["id"], identity))
        task.update(revision=saved['revision'], updated_at=saved['updated_at'])
        return task

    def backup_package(self, task):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM backup_packages WHERE task_id=?", (task["id"],)).fetchone()
        info = task.get("backup_package")
        if (not row or not isinstance(info, dict) or not isinstance(row[0], bytes)
                or len(row[0]) != info.get("byte_count") or hashlib.sha256(row[0]).hexdigest() != info.get("sha256")):
            raise ArchiveRecordError("已导入的ZIP备份缺失或内容已变化")
        return row[0]

    @staticmethod
    def _original_id(task, kind, name, digest, directory=None):
        return "rh_original_" + fingerprint([task["id"], task["version"], kind, Path(name).name, digest, directory])[:24]

    def _original_value(self, task, kind, name, payload, directory=None):
        """Build a byte-bound receipt shared by checkpoints and related creation."""
        if kind not in ("material", "filing") or not isinstance(name, str) or not name or not isinstance(payload, bytes):
            raise ContractError("原件身份或字节无效")
        if Path(name).suffix.lower() not in ORIGINAL_EXTENSIONS or len(payload) > ORIGINAL_LIMIT:
            raise ContractError("仅留存25MB以内的PDF、TXT、Markdown、DOCX和HTML原件")
        digest = hashlib.sha256(payload).hexdigest()
        identity = self._original_id(task, kind, name, digest, directory)
        return {"id": identity, "task_id": task["id"], "research_version": task["version"], "kind": kind,
                 "name": Path(name).name, "sha256": digest, "byte_count": len(payload), "directory": directory,
                 "original_base64": base64.b64encode(payload).decode(), "retained_at": now()}

    def create_related(self, task, parent):
        """Commit an unstarted child, its ownership and originals as one unit.

        Original bytes already exist in the parent archive. Preparation can fail
        without losing them; no empty child is needed as a parsing checkpoint.
        """
        if (task.get('parent') != parent.get('id') or task.get('status') != '待开始'
                or task.get('revision') != 0 or task.get('version') != 1
                or task.get('owner_account') != parent.get('owner_account')):
            raise ContractError('关联研究初始状态或归属不一致，未保存任务')
        originals = []
        for record in task.get('materials', []):
            if record.get('task_id') != task['id'] or record.get('research_version') != 1 or record.get('kind') != 'material':
                raise ContractError('关联研究原件不属于新任务，未保存任务')
            try:
                payload = base64.b64decode(record['original_base64'], validate=True)
            except (KeyError, ValueError, TypeError):
                raise ContractError('关联研究原件字节格式无效，未保存任务') from None
            value = self._original_value(task, 'material', record['name'], payload)
            if value['sha256'] != record.get('hash'):
                raise ContractError('关联研究原件指纹不一致，未保存任务')
            originals.append((value, record['id']))
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision,body FROM tasks WHERE id=?', (parent['id'],)).fetchone()
            if not row or row[0] != parent['revision'] or canonical(decode_body(db, row[1])) != canonical(parent):
                raise ContractError('原研究已更新，请重新选择原件，未保存关联任务')
            saved = dict(task, revision=1, updated_at=now())
            body = encode_body(db, saved)
            # Plain INSERT prevents accidental replacement of an existing task.
            db.execute('INSERT INTO tasks VALUES(?,?,?)', (task['id'], 1, body))
            self._store_workspace_summary(db, saved, body)
            db.execute('INSERT INTO versions VALUES(?,?,?)', (task['id'], 1, body))
            for value, artifact_id in originals:
                db.execute('INSERT INTO originals(id,task_id,version,body,artifact_id) VALUES(?,?,?,?,?)',
                    (value['id'], task['id'], 1, encode_body(db, value), artifact_id))
        task.update(revision=saved['revision'], updated_at=saved['updated_at'])
        return task

    def retain_original(self, task, kind, name, payload, directory=None):
        """Durable raw checkpoint before any parser starts; not research evidence."""
        value = self._original_value(task, kind, name, payload, directory)
        identity, digest = value['id'], value['sha256']
        with self.lock, self.connect() as db:
            if not db.execute("SELECT 1 FROM versions WHERE task_id=? AND version=?", (task["id"], task["version"])).fetchone():
                raise ContractError("原件所属任务版本不存在，未启动解析")
            artifact_id = None
            for record in task.get("materials", []) + task.get("pending_materials", []) + task.get("source_records", []) + task.get("pending_source_records", []):
                material = record if kind == "material" else record.get("snapshot", {}).get("material", {})
                if (record.get("kind") == kind and record.get("task_id") == task["id"]
                        and record.get("research_version") == task["version"]
                        and material.get("name") == value["name"] and material.get("hash") == digest
                        and record.get("directory") == directory):
                    artifact_id = record["id"]
                    break
            db.execute("INSERT OR IGNORE INTO originals(id,task_id,version,body,artifact_id) VALUES(?,?,?,?,?)",
                       (identity, task["id"], task["version"], encode_body(db, value), artifact_id))
        return identity

    def link_material_copy(self, task, original_id, artifact_id):
        """Link a retained filename to its equivalent parsed material."""
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            revision = db.execute("SELECT revision FROM tasks WHERE id=?", (task["id"],)).fetchone()
            if revision != (task["revision"],):
                raise ContractError("任务已更新，原件仍可重新解析，未关联旧材料")
            linked = db.execute(
                "UPDATE originals SET artifact_id=? WHERE id=? AND task_id=? AND version=?",
                (artifact_id, original_id, task["id"], task["version"]))
            if linked.rowcount != 1:
                raise ContractError("副本原件不属于当前任务版本")

    @staticmethod
    def _parsed_catalogue_originals(task):
        """Recognize losslessly retained directory bytes, not document evidence.

        Old captures may not have a database artifact link. Require the exact
        canonical filename, byte hash and one task-owned parsed API snapshot.
        A separately named upload keeps its own material-processing state.
        """
        from .official_sources import catalogue_original_name, validate_request

        captures = {}
        for record in task.get('source_records', []) + task.get('pending_source_records', []):
            if (record.get('kind') != 'api' or record.get('source') != 'official'
                    or record.get('operation') != 'release_catalog' or record.get('status') != '成功待核验'
                    or record.get('task_id') != task['id'] or record.get('research_version') != task['version']):
                continue
            snapshot, parameters = record.get('snapshot'), record.get('parameters')
            if not isinstance(snapshot, dict) or not isinstance(parameters, dict):
                continue
            try:
                digest = fingerprint(snapshot)
                request_hash = fingerprint(dict(source='official', operation='release_catalog', parameters=parameters))
                expected_id = 'rh_official_' + fingerprint([task['id'], task['version'], request_hash, digest])[:24]
                if (record.get('hash') != digest or record.get('request_hash') != request_hash
                        or record.get('id') != expected_id or not isinstance(snapshot.get('entries'), list)
                        or not isinstance(snapshot.get('coverage'), dict)):
                    continue
                validate_request(task, 'release_catalog', parameters)
                name = catalogue_original_name(parameters, request_hash)
                payload = base64.b64decode(snapshot['original_base64'], validate=True)
                actual_hash = hashlib.sha256(payload).hexdigest()
                if snapshot.get('original_sha256') != actual_hash:
                    continue
            except (ContractError, KeyError, TypeError, ValueError):
                continue
            captures.setdefault((name, actual_hash, len(payload)), []).append(record['id'])
        return captures

    def _original_headers(self, task, *, history=False):
        """Read file identity from the receipt, independently of the bytes."""
        comparator = "<=" if history else "="
        def valid(body, identity, version):
            return (isinstance(body, dict) and body.get("id") == identity and body.get("task_id") == task["id"]
                    and type(body.get("research_version")) is int and body["research_version"] == version
                    and body.get("kind") in ("material", "filing") and isinstance(body.get("name"), str)
                    and Path(body["name"]).suffix.lower() in ORIGINAL_EXTENSIONS and isinstance(body.get("sha256"), str)
                    and SHA256.fullmatch(body["sha256"]) and type(body.get("byte_count")) is int
                    and 0 <= body["byte_count"] <= ORIGINAL_LIMIT and isinstance(body.get("retained_at"), str)
                    and self._original_id({"id": task["id"], "version": version}, body["kind"],
                                          body["name"], body["sha256"], body.get("directory")) == identity)
        result = []
        with self.connect() as db:
            rows = db.execute("SELECT id,version,body,artifact_id FROM originals "
                              f"WHERE task_id=? AND version{comparator}? ORDER BY version DESC,rowid",
                              (task["id"], task["version"]))
            for identity, version, wire, artifact_id in rows:
                try:
                    body = json.loads(wire)
                    if not valid(body, identity, version):
                        # Older receipts can also externalize directory metadata.
                        body = decode_body(db, wire)
                        if not valid(body, identity, version):
                            continue
                    result.append(dict({key: body[key] for key in ORIGINAL_METADATA}, artifact_id=artifact_id))
                except (ValueError, TypeError, KeyError, RecursionError):
                    continue
        return result

    def retained_originals(self, task, *, pending_only=False):
        """Metadata only: no bytes, body extracts, credentials or approval flags."""
        from .pdf_material import legacy_pdf_text_damaged

        result = []
        excluded = {(row["name"], row["sha256"]) for row in task.get("material_exclusions", [])}
        independent = {row["id"] for row in task.get("source_records", []) + task.get("pending_source_records", [])}
        records = {record["id"]: record for record in task.get("materials", []) + task.get("pending_materials", []) + task.get("source_records", []) + task.get("pending_source_records", [])}
        catalogue_captures = None
        for body in self._original_headers(task):
            artifact_id = body["artifact_id"]
            if body["kind"] == "material" and artifact_id not in independent and (body["name"], body["sha256"]) in excluded:
                if not pending_only:
                    result.append(dict(body, excluded_before_start=True,
                        status="已移出本次研究，原件保留"))
                continue
            record = records.get(artifact_id, {})
            material = record.get("snapshot", record) if record.get("kind") == "material" else record.get("snapshot", {}).get("material", {})
            damaged = legacy_pdf_text_damaged(material)
            parsed = artifact_id and material.get("status") == "已解析" and not damaged
            catalogue = False
            if (not parsed and (not record or record.get('kind') == 'api'
                    and record.get('source') == 'official' and record.get('operation') == 'release_catalog')
                    and body.get('kind') == 'material'
                    and Path(body.get('name', '')).suffix.lower() in ('.html', '.htm')):
                if catalogue_captures is None:
                    catalogue_captures = self._parsed_catalogue_originals(task)
                matches = catalogue_captures.get((body['name'], body['sha256'], body['byte_count']), [])
                if len(matches) == 1 and (not record or matches[0] == artifact_id):
                    artifact_id, catalogue, parsed = matches[0], True, True
            if pending_only and parsed:
                continue
            status = ("原始目录已归档" if catalogue else "原件已保存，文字编码需重解析" if damaged else "已归档，未授予核验" if parsed else
                      "原件已保存，解析受限" if artifact_id else "原件已保存，解析未归档")
            result.append(dict(body, artifact_id=artifact_id, status=status))
        return result

    def submitted_material_names(self, task):
        active = {row.get('id'): row.get('hash') for row in task.get('materials', [])
                  if row.get('kind') == 'material' and row.get('source') == '用户提供材料'
                  and row.get('task_id') == task['id'] and row.get('research_version') == task['version']
                  and isinstance(row.get('id'), str)}
        if not active:
            return []
        excluded = {(row['name'], row['sha256']) for row in task.get('material_exclusions', [])}
        # Read original receipts to preserve submitted filenames when several
        # names share parsed content. Only include files linked to this input.
        return [row['name'] for row in self._original_headers(task)
                if row['kind'] == 'material' and active.get(row['artifact_id']) == row['sha256']
                and (row['name'], row['sha256']) not in excluded]

    @staticmethod
    def _snapshot_files(task, *, origin_versions=False):
        """Read older snapshots that retained bytes inside the source record."""
        files, ambiguous = {}, set()
        for _, _, record, material, source_version in embedded_materials(task):
            ident, kind = record.get("id"), record["kind"]
            if (not isinstance(ident, str) or not ident or ident.startswith("rh_original_")
                    or record["research_version"] != task["version"]):
                continue
            name, digest, wire = material.get("name"), material.get("hash"), material.get("original_base64")
            if (not isinstance(name, str) or Path(name).suffix.lower() not in ORIGINAL_EXTENSIONS
                    or not isinstance(digest, str) or not SHA256.fullmatch(digest)
                    or not isinstance(wire, str) or len(wire) % 4):
                continue
            size = len(wire) // 4 * 3 - (2 if wire.endswith("==") else 1 if wire.endswith("=") else 0)
            if not 0 <= size <= ORIGINAL_LIMIT:
                continue
            version = source_version if origin_versions else task["version"]
            row = dict(id=ident, artifact_id=ident, task_id=task["id"], research_version=version,
                       kind=kind, name=name, sha256=digest, byte_count=size)
            if ident in files and files[ident] != (row, wire):
                ambiguous.add(ident)
            files[ident] = (row, wire)
        return {ident: value for ident, value in files.items() if ident not in ambiguous}

    def original_files(self, task):
        files = self._original_headers(task, history=True)
        bindings = {(item["name"], item["sha256"], item["artifact_id"]) for item in files}
        files.extend(row for row, _ in self._snapshot_files(task).values()
                     if (row["name"], row["sha256"], row["artifact_id"]) not in bindings)
        return files

    def read_original_file(self, task, version, identity):
        if not 0 < version <= task["version"]:
            raise ContractError("留存原件版本无效")
        if identity.startswith("rh_original_"):
            return self.original({"id": task["id"], "version": version}, identity)[1]
        source = task if version == task["version"] else self.get(task["id"], version)
        saved = self._snapshot_files(source).get(identity)
        if saved is None:
            raise ContractError("当前版本未留存该原件")
        row, wire = saved
        return embedded_original_bytes(wire, row["sha256"], row["byte_count"])

    def original(self, task, identity):
        if not isinstance(identity, str) or not identity.startswith("rh_original_"):
            raise ContractError("原件留存标识无效")
        with self.connect() as db:
            row = db.execute("SELECT body FROM originals WHERE id=? AND task_id=? AND version=?",
                             (identity, task["id"], task["version"])).fetchone()
            decoded = decode_body(db, row[0]) if row else None
        if not row:
            raise ContractError("原件留存记录不属于当前任务版本")
        try:
            value = decoded
            if not isinstance(value, dict) or value.get("kind") not in ("material", "filing") or not isinstance(value.get("name"), str):
                raise ValueError("invalid original identity")
            payload = base64.b64decode(value["original_base64"], validate=True)
        except (ValueError, TypeError, KeyError):
            raise ContractError("留存原件字节格式无效") from None
        if (value.get("task_id") != task["id"] or value.get("research_version") != task["version"]
                or len(payload) > ORIGINAL_LIMIT
                or value.get("byte_count") != len(payload) or value.get("sha256") != hashlib.sha256(payload).hexdigest()
                or self._original_id(task, value.get("kind"), value.get("name", ""), value.get("sha256"), value.get("directory")) != identity):
            raise ContractError("留存原件身份或完整性不一致，不能重新解析")
        return value, payload

    def get(self, task_id, version=None, *, allow_missing=False):
        with self.connect() as db:
            if version is None:
                row = db.execute("SELECT revision,body FROM tasks WHERE id=?", (task_id,)).fetchone()
                if row:
                    return _read_task(db, row[1], task_id, revision=row[0])
            else:
                row = db.execute("SELECT version,body FROM versions WHERE task_id=? AND version=?", (task_id, version)).fetchone()
                if row:
                    return _read_task(db, row[1], task_id, version=row[0])
        if allow_missing:
            return None
        raise ContractError("任务或研究版本不存在")

    def list(self):
        with self.connect() as db:
            return [_read_task(db, body, ident, revision=revision) for ident,revision,body in
                    db.execute("SELECT id,revision,body FROM tasks ORDER BY rowid DESC")]

    @staticmethod
    def _workspace_index_values(task, body):
        summary = canonical(workspace_task_summary(task))
        return (task['id'], task['revision'], hashlib.sha256(body.encode()).hexdigest(),
                SUMMARY_VERSION, summary, hashlib.sha256(summary.encode()).hexdigest())

    @classmethod
    def _store_workspace_summary(cls, db, task, body):
        # The task row and its UI metadata commit or roll back together.
        db.execute('INSERT OR REPLACE INTO workspace_summaries VALUES(?,?,?,?,?,?)',
                   cls._workspace_index_values(task, body))

    def list_workspace(self, *, unreadable=None, task_id=None):
        """Read saved UI headers without inflating untouched source payloads.

        A cache is tied to the exact encoded body, revision and projection.
        Missing/invalid caches use the ordinary complete decoder. Selecting a
        task always keeps get(), which independently checks all source values.
        The workspace may collect unreadable row identities and continue. No
        title or owner is inferred from a damaged, unverified body.
        """
        summaries, missing = [], []
        with self.connect() as db:
            rows = db.execute("""SELECT t.id,t.revision,t.body,
                s.task_revision,s.body_sha256,s.view_version,s.summary,s.summary_sha256
                FROM tasks t LEFT JOIN workspace_summaries s ON s.task_id=t.id
                WHERE (? IS NULL OR t.id=?) ORDER BY t.rowid DESC""", (task_id, task_id))
            for ident, revision, body, cached_revision, body_hash, view_version, raw, summary_hash in rows:
                summary = None
                digest = hashlib.sha256(body.encode()).hexdigest() if isinstance(body, str) else None
                if (digest is not None and revision == cached_revision and type(view_version) is int and view_version == SUMMARY_VERSION
                        and body_hash == digest and isinstance(raw, str)
                        and hashlib.sha256(raw.encode()).hexdigest() == summary_hash):
                    try:
                        candidate = json.loads(raw)
                    except (TypeError, ValueError):
                        candidate = None
                    if valid_cached_summary(candidate, ident, revision):
                        summary = candidate
                if summary is None:
                    try:
                        task = _read_task(db, body, ident, revision=revision)
                    except ArchiveRecordError:
                        if unreadable is None:
                            raise
                        unreadable[ident] = revision
                        continue
                    summary = workspace_task_summary(task)
                    # A concurrent save must never be overwritten by lazy
                    # indexing of an older snapshot, even at the same revision.
                    missing.append((self._workspace_index_values(task, body), body))
                summaries.append(summary)
        if missing:
            try:
                with self.connect() as db:
                    db.execute('PRAGMA busy_timeout=0')
                    db.execute('BEGIN IMMEDIATE')
                    for values, body in missing:
                        db.execute("""INSERT OR REPLACE INTO workspace_summaries
                            SELECT ?,?,?,?,?,? FROM tasks WHERE id=? AND revision=? AND body=?""",
                                   (*values, values[0], values[1], body))
            except sqlite3.OperationalError:
                # Read-only or busy legacy stores can still return the fully
                # decoded headers. This optional backfill has no task writes.
                pass
        return summaries

    def presentations(self, tasks):
        """UI metadata only; never alter research bodies or frozen deliveries."""
        keys = {(task.get('owner_account') or '', task.get('thread_id') or task['id']) for task in tasks}
        with self.connect() as db:
            rows = db.execute('SELECT owner_account,thread_id,body FROM task_presentations').fetchall()
        presentations = {}
        for owner, thread, body in rows:
            if (owner, thread) not in keys:
                continue
            try:
                value = json.loads(body)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                presentations[thread] = value
        return presentations

    def update_presentation(self, ident, payload):
        if not isinstance(payload, dict) or set(payload) != {'expected_revision', 'changes'}:
            raise ContractError('任务展示设置格式无效')
        expected, changes = payload['expected_revision'], payload['changes']
        if type(expected) is not int or expected < 0 or not isinstance(changes, dict) or not changes or set(changes) - {'title', 'pinned', 'archived'}:
            raise ContractError('任务展示设置或修订号无效')
        if 'title' in changes:
            title = changes['title']
            if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120 or any(ord(char) < 32 or ord(char) == 127 for char in title):
                raise ContractError('任务名称须为1至120个字符，不能包含换行或控制字符')
            changes = dict(changes, title=title.strip())
        if any(type(changes[field]) is not bool for field in ('pinned', 'archived') if field in changes):
            raise ContractError('置顶和归档状态须为布尔值')
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT body FROM tasks WHERE id=?', (ident,)).fetchone()
            if not row:
                raise ContractError('任务不存在')
            task = decode_body(db, row[0]);thread = task.get('thread_id') or ident;owner = task.get('owner_account') or ''
            root = db.execute('SELECT body FROM tasks WHERE id=?', (thread,)).fetchone()
            if not root or (decode_body(db, root[0]).get('owner_account') or '') != owner:
                raise ContractError('关联研究归属不一致，未修改展示设置')
            if changes.get('archived') is True:
                members = self.list_workspace(unreadable={})
                if any((item.get('thread_id') or item['id']) == thread and item.get('status') == '运行中' for item in members):
                    raise ContractError('研究仍在运行，请先取消或等待完成后归档')
            saved = db.execute('SELECT revision,body FROM task_presentations WHERE owner_account=? AND thread_id=?', (owner,thread)).fetchone()
            revision = saved[0] if saved else 0
            if expected != revision:
                raise ContractError('任务展示设置已更新，请重新读取后修改')
            value = json.loads(saved[1]) if saved else {'title':'', 'pinned':False, 'archived':False}
            value.update(changes, revision=revision + 1, thread_id=thread)
            db.execute('INSERT OR REPLACE INTO task_presentations VALUES(?,?,?,?)', (owner,thread,revision + 1,canonical(value)))
        return value

    @staticmethod
    def _workspace_draft_row(db, owner):
        return db.execute('SELECT revision,write_id,body FROM workspace_drafts WHERE owner_account=?', (owner,)).fetchone()

    def workspace_draft(self, owner):
        try:
            with self.connect() as db:
                row = self._workspace_draft_row(db, owner)
            if row is None:
                return {'revision': 0, 'write_id': None, 'state': {}}
            revision, write_id, body = row
            state = json.loads(body)
            if type(revision) is not int or not valid_workspace_draft_write(revision - 1, write_id):
                raise ValueError
            validate_workspace_draft(state)
            return {'revision': revision, 'write_id': write_id, 'state': state}
        except (sqlite3.Error, TypeError, ValueError, ContractError) as error:
            raise ArchiveRecordError('上次草稿无法读取，当前工作台仍可使用') from error

    def save_workspace_draft(self, owner, expected_revision, write_id, state):
        if not valid_workspace_draft_write(expected_revision, write_id):
            raise ContractError('草稿修订号或保存回执无效')
        validate_workspace_draft(state)
        body = canonical(state)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._workspace_draft_row(db, owner)
            revision, previous_write, previous_body = row or (0, None, '{}')
            if expected_revision <= revision and body == previous_body:
                # The same snapshot after a lost acknowledgement is already saved.
                return {'revision': revision, 'write_id': previous_write}
            if revision != expected_revision or write_id == previous_write:
                raise WorkspaceDraftConflict('另一窗口已保存更新的草稿')
            revision += 1
            db.execute('INSERT OR REPLACE INTO workspace_drafts VALUES(?,?,?,?)', (owner, revision, write_id, body))
        return {'revision': revision, 'write_id': write_id}

    def versions(self, task_id):
        with self.connect() as db:
            return [row[0] for row in db.execute("SELECT version FROM versions WHERE task_id=? ORDER BY version", (task_id,))]

    def delivery_versions(self, task_id):
        """List saved reports, not the checkpoints retained for cancellation/recovery."""
        with self.connect() as db:
            return [row[0] for row in db.execute(
                "SELECT version FROM deliveries WHERE task_id=? ORDER BY version", (task_id,))]

    def task_backup(self, task):
        """Collect the current checkpoint, frozen reports and retained original bytes."""
        ident = task["id"]
        with self.connect() as db:
            originals = db.execute(
                "SELECT id,version FROM originals WHERE task_id=? AND version<=? ORDER BY version,rowid",
                (ident, task["version"])).fetchall()
        reports = []
        delivered = self.delivery_versions(ident)
        for version in delivered:
            if version <= task["version"]:
                report = self.report(ident, version)
                reports.append(dict(report, version=version,
                                    html_sha256=hashlib.sha256(report["html"].encode()).hexdigest()))
        original_files = [self.original({"id": ident, "version": version}, original_id)[0]
                          for original_id, version in originals]
        seen = {(row["research_version"], row["name"], row["sha256"]) for row in original_files}
        snapshot_versions = [version for version in self.versions(ident)
                             if version < task["version"] or version == task["version"] and version in delivered]
        for version in [None, *snapshot_versions]:
            try:
                snapshot = task if version is None else self.get(ident, version)
            except ArchiveRecordError:
                continue
            if snapshot.get("owner_account") != task.get("owner_account"):
                continue
            for row, wire in self._snapshot_files(snapshot, origin_versions=True).values():
                key = (row["research_version"], row["name"], row["sha256"])
                if key in seen:
                    continue
                try:
                    embedded_original_bytes(wire, row["sha256"], row["byte_count"])
                except ContractError:
                    continue
                original_files.append(dict(row, original_base64=wire))
                seen.add(key)
        return {
            "workspaceVersion": 4,
            "tasks": [task],
            "reports": reports,
            "originals": sorted(original_files, key=lambda row: row["research_version"]),
        }

    def latest_delivery_versions(self):
        """Index saved editions for the authorized workspace catalogue."""
        with self.connect() as db:
            return dict(db.execute("SELECT task_id,MAX(version) FROM deliveries GROUP BY task_id"))

    @staticmethod
    def _legacy_delivery_title(db, row_id, task_id, version, digest):
        saved = db.execute("""
            SELECT json_extract(body,'$.delivery.title'), json_extract(body,'$.delivery.html_sha256'),
                   coalesce(json_type(body,'$.delivery.html_sha256'),'missing')
            FROM versions WHERE task_id=? AND version=? AND CASE WHEN json_valid(body) THEN
                json_extract(body,'$.id')=task_id AND json_extract(body,'$.version')=version
                AND json_extract(body,'$.delivery.version')=version AND json_extract(body,'$.delivery.digest')=?
            ELSE 0 END
        """, (task_id, version, digest)).fetchone()
        if saved is None:
            return None
        title, html_digest, hash_type = saved
        if isinstance(title, str) and title.strip():
            return ' '.join(title.split())
        if hash_type not in ('missing', 'null') and (
                not isinstance(html_digest, str) or not SHA256.fullmatch(html_digest)):
            return None
        # Old stores may lack the display title. Read the frozen HTML header;
        # verify any recorded HTML hash without decoding its report or evidence.
        try:
            with db.blobopen('deliveries', 'html', row_id, readonly=True) as page:
                head = page.read(REPORT_HEAD_BYTES).partition(b'</head>')[0]
                match = re.search(rb'<title>([^<]*)</title>', head)
                if not match:
                    return None
                if html_digest is not None:
                    page.seek(0)
                    hasher = hashlib.sha256()
                    for chunk in iter(lambda: page.read(65536), b''):
                        hasher.update(chunk)
                    if hasher.hexdigest() != html_digest:
                        return None
            return ' '.join(unescape(match[1].decode('utf-8')).split()) or None
        except (sqlite3.OperationalError, UnicodeDecodeError):
            return None

    def delivery_history(self, task_id, *, version=None):
        """Read report identities and their annotations from one archive snapshot.

        Checkpoints and another task's corrections never enter this list. Full
        report bodies are not decoded just to open the version selector.
        """
        parameters = (task_id,) if version is None else (task_id, version)
        scope = " WHERE d.task_id=?" + (" AND d.version=?" if version is not None else "")
        with self.connect() as db:
            db.execute("BEGIN")
            versions = db.execute(
                "SELECT d.rowid,d.version,d.digest,d.created_at,t.title FROM deliveries d "
                "LEFT JOIN delivery_titles t ON t.task_id=d.task_id AND t.version=d.version AND t.digest=d.digest"
                + scope + " ORDER BY d.version DESC", parameters).fetchall()
            notes = db.execute(
                "SELECT c.version,c.reason,c.created_at FROM corrections c "
                "JOIN deliveries d ON c.task_id=d.task_id AND c.version=d.version "
                + scope + " ORDER BY c.rowid", parameters).fetchall()
            by_version = {}
            for edition, reason, created_at in notes:
                by_version.setdefault(edition, []).append(dict(
                    task_id=task_id, version=edition, reason=reason, created_at=created_at))
            history = []
            for row_id, edition, digest, created_at, title in versions:
                record = dict(task_id=task_id, version=edition, digest=digest, created_at=created_at,
                              corrections=by_version.get(edition, []))
                title = title or self._legacy_delivery_title(db, row_id, task_id, edition, digest)
                if title:
                    record['title'] = title
                history.append(record)
            return history

    def deliver(self, task, content, html, *, execution_timer=None):
        """State and all dependency snapshots commit in the same transaction."""
        from .reports import check_content, clear_pending_html, compose, pending_html_sha256
        if not isinstance(content, dict):
            raise ContractError("交付内容须为规范报告对象，未冻结报告")
        check_content(content)
        digest = fingerprint(content)
        if digest != fingerprint(compose(task)):
            raise ContractError("交付内容与当前研究依据不一致，未冻结报告")
        expected_html = task.get("pending_html")
        html_sha256 = None
        if expected_html is None and task.get("delivery"):
            # Completed tasks no longer carry a second copy of the paper.
            # An idempotent call must still match the verified frozen bytes.
            expected_html = self.report(task["id"], task["version"])["html"]
        else:
            html_sha256 = pending_html_sha256(task, digest)
            if html_sha256 is None:
                raise ContractError("待保存页面与同版呈现检查点不一致，请重试保存")
        if not isinstance(html, str) or not html.strip() or html != expected_html:
            raise ContractError("交付页面与已保存的呈现检查点不一致，未冻结报告")
        if task.get("version_changes_contract") is not None:
            from .reports import validate_version_changes
            validate_version_changes(task, self.report(task["id"], task["update_from"]["version"]))
            if content.get("changes") != task["changes"] or content.get("update_from") != task["update_from"]:
                raise ContractError("交付版本差异与任务不一致")
        if task.get('judgment_review_contract') is not None:
            from .judgment_review import validate_saved
            validate_saved(task, self.report(task['id'], task['update_from']['version']))
        with self.lock, self.connect() as db:
            # Reserve the SQLite writer before checking the durable revision.
            # A Python lock alone cannot exclude another archive connection.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision,body FROM tasks WHERE id=?", (task["id"],)).fetchone()
            if not row or row[0] != task["revision"] or canonical(decode_body(db, row[1])) != canonical(task):
                raise ContractError("交付输入已过期")
            prior = db.execute("SELECT digest FROM deliveries WHERE task_id=? AND version=?",
                               (task["id"], task["version"])).fetchone()
            if prior:
                if prior[0] != digest:
                    raise ContractError("已冻结交付不可覆盖")
                # Validate the existing freeze before declaring a save retry
                # idempotent. Corrupted presentation must not become success.
                self.report(task["id"], task["version"])
                return self.get(task["id"])
            if html_sha256 is None:
                raise ContractError("首次交付缺少同版呈现检查点，请重试保存")
            saved_at = now()
            is_draft = content.get('report_kind') == 'research_draft'
            required = [question for question in task["questions"] if question["required"]]
            direct_query_complete = (task.get('run_mode') in ('direct', 'offline', 'local_draft')
                and bool(required) and all(question['status'] == '已回答' and question.get('provenance_integrity')
                                           for question in required))
            direct_study_complete = False
            if is_draft and task.get(PARALLEL_STUDY_POLICY) == 1 and len(required) == 2:
                from .parallel_readout import verified_answers

                answers = verified_answers(task, task['draft_report'])
                direct_study_complete = all(question['status'] == '已回答' and question['id'] in answers
                    and question.get('parallel_answer') == answers[question['id']]
                    and question.get('answer') == answers[question['id']]['answer'] for question in required)
            elif is_draft and len(required) == 1:
                from .chain_readout import verified_chain_answer
                from .lpr_readout import verified_lpr_answer
                from .macro_readout import verified_macro_answer
                from .stock_quality_readout import verified_stock_quality_answer
                from .draft_readout import verified_etf_answer, verified_event_market_answer, verified_industry_answer

                question, = required
                draft = task['draft_report']
                direct_study_complete = (question['status'] == '已回答'
                    and question.get('answer') == draft['conclusion']
                    and any(question.get(key) and question[key] == verify(task, draft) for key, verify in (
                        ('industry_answer', verified_industry_answer), ('etf_answer', verified_etf_answer),
                        ('event_market_answer', verified_event_market_answer),
                        ('industry_chain_answer', verified_chain_answer),
                        ('macro_demand_answer', verified_macro_answer),
                        ('lpr_answer', verified_lpr_answer),
                        ('stock_quality_answer', verified_stock_quality_answer))))
            incomplete = task["gaps"] or (is_draft and not (direct_query_complete or direct_study_complete))
            saved = dict(task, status="部分完成" if incomplete else "已完成", error=None,
                         revision=task["revision"] + 1, updated_at=saved_at,
                         delivery={"version": task["version"], "digest": digest,
                                   "html_sha256": html_sha256, "saved_at": saved_at})
            title = task['pending_html_binding'].get('title')
            title = ' '.join(title.split()) if isinstance(title, str) else None
            if is_draft:
                saved['delivery']['kind'] = 'research_draft'
                title = title or ' '.join(content['title'].split())
            elif not title:
                from .knowledge import definition_title
                title = definition_title(content)
            if title:
                saved['delivery']['title'] = title
            # Checkpoints remain durable on every failed transaction. Only a
            # successful freeze replaces them with the authoritative delivery.
            saved.pop("pending_content", None)
            saved.pop('draft_composition_binding', None)
            clear_pending_html(saved)
            db.execute("INSERT INTO deliveries VALUES(?,?,?,?,?,?)",
                       (task["id"], task["version"], encode_body(db, content), html, digest, saved_at))
            if title:
                db.execute("INSERT INTO delivery_titles(task_id,version,digest,title) VALUES(?,?,?,?)",
                           (task['id'], task['version'], digest, title))
            if execution_timer is not None:
                # Include validation and writer wait without modifying the
                # pending checkpoint or exposing an incomplete freeze.
                saved["ledger"] = dict(task["ledger"], used=dict(task["ledger"]["used"]))
                execution_timer.charge(saved["ledger"], delivery=True)
            body = encode_body(db, saved)
            db.execute("UPDATE tasks SET revision=?,body=? WHERE id=?", (saved["revision"], body, task["id"]))
            self._store_workspace_summary(db, saved, body)
            db.execute("INSERT OR REPLACE INTO versions VALUES(?,?,?)", (task["id"], task["version"], body))
        return saved

    def report(self, task_id, version):
        with self.connect() as db:
            # Read content, corrections and frozen metadata from one snapshot.
            db.execute("BEGIN")
            row = db.execute("SELECT content,html,digest FROM deliveries WHERE task_id=? AND version=?",
                             (task_id, version)).fetchone()
            corrections = db.execute("SELECT reason,created_at FROM corrections WHERE task_id=? AND version=?",
                                     (task_id, version)).fetchall()
            frozen = db.execute("SELECT body FROM versions WHERE task_id=? AND version=?",
                                (task_id, version)).fetchone()
            decode = snapshot_decoder(db)
            try:
                content, verified_digest = decode(row[0]) if row else (None, None)
                saved = _read_task(db, frozen[0], task_id, version=version, decode=decode) if frozen else {}
            except (TypeError, ValueError, KeyError):
                raise ContractError("冻结报告记录格式无效，未返回报告") from None
            finally:
                del decode
        if not row:
            raise ContractError("该版本尚未保存交付")
        try:
            from .reports import check_content
            if not isinstance(content, dict):
                raise ContractError("冻结报告内容格式无效，未返回报告")
            check_content(content)
            if not isinstance(saved, dict):
                raise ContractError("冻结研究版本格式无效，未返回报告")
            delivery = saved.get("delivery") or {}
            if not isinstance(delivery, dict):
                raise ContractError("冻结交付元数据格式无效，未返回报告")
            if ((verified_digest or fingerprint(content)) != row[2] or delivery.get("digest") != row[2]
                    or delivery.get("version") != version or content.get("task_id") != task_id
                    or content.get("research_version") != version
                    or content.get("input_hash") != saved.get("input_hash")
                    or not isinstance(row[1], str) or not row[1].strip()):
                raise ContractError("冻结报告内容或版本身份已变化，未返回报告")
            html_hash = delivery.get("html_sha256")
            if html_hash is not None and html_hash != hashlib.sha256(row[1].encode()).hexdigest():
                raise ContractError("冻结报告页面已变化，未返回报告")
        except (TypeError, ValueError, KeyError):
            raise ContractError("冻结报告记录格式无效，未返回报告") from None
        # Older deliveries remain readable; never invent a presentation hash
        # for bytes that were not independently frozen when originally saved.
        return {"content": content, "html": row[1], "digest": row[2], "corrections": corrections,
                "html_integrity_verified": html_hash is not None}

    def correction_records(self, task_id, version):
        """Read version-bound annotations without rewriting a frozen delivery."""
        if type(version) is not int or version < 1:
            raise ContractError("报告版本无效")
        with self.connect() as db:
            rows = db.execute("SELECT reason,created_at FROM corrections WHERE task_id=? AND version=? ORDER BY rowid",
                              (task_id, version)).fetchall()
        return [dict(task_id=task_id, version=version, reason=reason, created_at=created_at)
                for reason, created_at in rows]

    def correction(self, task_id, version, reason, *, expected_digest=None):
        """Append a correction once; retries never alter the original report."""
        if type(version) is not int or version < 1:
            raise ContractError("报告版本无效")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
            raise ContractError("请输入4000字以内的更正说明")
        reason = reason.strip()
        report = self.report(task_id, version)
        if expected_digest is not None and expected_digest != report['digest']:
            raise ContractError("所选报告已变化，请重新打开后记录更正")
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            frozen = db.execute("SELECT digest FROM deliveries WHERE task_id=? AND version=?",
                                (task_id, version)).fetchone()
            if not frozen or frozen[0] != report['digest']:
                raise ContractError("所选报告已变化，未记录更正")
            prior = db.execute("SELECT 1 FROM corrections WHERE task_id=? AND version=? AND reason=?",
                               (task_id, version, reason)).fetchone()
            if not prior:
                db.execute("INSERT INTO corrections VALUES(?,?,?,?)", (task_id, version, reason, now()))
        return self.correction_records(task_id, version)


def project_workspace_path(project, installation, *parts):
    """Resolve a Skill data or output path within the selected project."""
    try:
        project, installation = Path(project).resolve(), Path(installation).resolve()
        destination = project.joinpath(*parts).resolve()
    except (OSError, RuntimeError):
        raise ContractError('无法定位研究目录，请检查项目内的数据和结果路径') from None
    if project.is_relative_to(installation):
        raise ContractError("Skill安装目录只读，研究项目必须独立")
    if (destination == project or not destination.is_relative_to(project)
            or destination.is_relative_to(installation)):
        raise ContractError('研究目录须位于所选项目内，且与Skill安装目录独立')
    return destination


def project_store(project, installation):
    return ResearchArchive(project_workspace_path(project, installation, 'data', 'researchhelper'))
