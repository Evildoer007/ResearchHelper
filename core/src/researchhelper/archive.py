"""Transactional, versioned local archive; installation and business stores stay separate."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path

from .contracts import ContractError, canonical, fingerprint, now
from .archive_codec import encode_body, decode_body


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
                CREATE TABLE IF NOT EXISTS corrections(task_id TEXT, version INTEGER, reason TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS originals(id TEXT PRIMARY KEY, task_id TEXT NOT NULL, version INTEGER NOT NULL,
                    body TEXT NOT NULL, artifact_id TEXT, FOREIGN KEY(task_id) REFERENCES tasks(id));
                CREATE TABLE IF NOT EXISTS task_presentations(owner_account TEXT NOT NULL, thread_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(owner_account,thread_id));
                CREATE TABLE IF NOT EXISTS archive_values(id TEXT PRIMARY KEY, byte_count INTEGER NOT NULL,
                    compressed BLOB NOT NULL);
            """)
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def save(self, task):
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT revision FROM tasks WHERE id=?", (task["id"],)).fetchone()
            revision = row[0] if row else 0
            if task.get("revision", 0) != revision:
                raise ContractError("任务已更新，请重新读取，不能覆盖较新版本")
            saved = dict(task, revision=revision + 1)
            body = encode_body(db, saved)
            db.execute("INSERT OR REPLACE INTO tasks VALUES(?,?,?)", (task["id"], revision + 1, body))
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
        task["revision"] = revision + 1
        return task

    @staticmethod
    def _original_id(task, kind, name, digest, directory=None):
        return "rh_original_" + fingerprint([task["id"], task["version"], kind, Path(name).name, digest, directory])[:24]

    def _original_value(self, task, kind, name, payload, directory=None):
        """Build a byte-bound receipt shared by checkpoints and related creation."""
        if kind not in ("material", "filing") or not isinstance(name, str) or not name or not isinstance(payload, bytes):
            raise ContractError("原件身份或字节无效")
        if Path(name).suffix.lower() not in (".pdf", ".docx", ".txt", ".md", ".html", ".htm") or len(payload) > 25 * 1024 * 1024:
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
        saved = dict(task, revision=1)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision,body FROM tasks WHERE id=?', (parent['id'],)).fetchone()
            if not row or row[0] != parent['revision'] or canonical(decode_body(db, row[1])) != canonical(parent):
                raise ContractError('原研究已更新，请重新选择原件，未保存关联任务')
            body = encode_body(db, saved)
            # Plain INSERT prevents accidental replacement of an existing task.
            db.execute('INSERT INTO tasks VALUES(?,?,?)', (task['id'], 1, body))
            db.execute('INSERT INTO versions VALUES(?,?,?)', (task['id'], 1, body))
            for value, artifact_id in originals:
                db.execute('INSERT INTO originals(id,task_id,version,body,artifact_id) VALUES(?,?,?,?,?)',
                    (value['id'], task['id'], 1, encode_body(db, value), artifact_id))
        task['revision'] = 1
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

    def retained_originals(self, task, *, pending_only=False):
        """Metadata only: no bytes, body extracts, credentials or approval flags."""
        with self.connect() as db:
            rows = db.execute("SELECT body,artifact_id FROM originals WHERE task_id=? AND version=? ORDER BY rowid",
                              (task["id"], task["version"])).fetchall()
            rows = [(decode_body(db, body), artifact_id) for body, artifact_id in rows]
        result = []
        records = {record["id"]: record for record in task.get("materials", []) + task.get("pending_materials", []) + task.get("source_records", []) + task.get("pending_source_records", [])}
        for body, artifact_id in rows:
            record = records.get(artifact_id, {})
            material = record if record.get("kind") == "material" else record.get("snapshot", {}).get("material", {})
            parsed = artifact_id and material.get("status") == "已解析"
            if pending_only and parsed:
                continue
            value = body
            result.append({key: value[key] for key in ("id", "task_id", "research_version", "kind", "name", "sha256", "byte_count", "retained_at")})
            result[-1].update(artifact_id=artifact_id, status="已归档，未授予核验" if parsed else "原件已保存，解析受限" if artifact_id else "原件已保存，解析未归档")
        return result

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
                or len(payload) > 25 * 1024 * 1024
                or value.get("byte_count") != len(payload) or value.get("sha256") != hashlib.sha256(payload).hexdigest()
                or self._original_id(task, value.get("kind"), value.get("name", ""), value.get("sha256"), value.get("directory")) != identity):
            raise ContractError("留存原件身份或完整性不一致，不能重新解析")
        return value, payload

    def get(self, task_id, version=None):
        with self.connect() as db:
            if version is None:
                row = db.execute("SELECT body FROM tasks WHERE id=?", (task_id,)).fetchone()
            else:
                row = db.execute("SELECT body FROM versions WHERE task_id=? AND version=?", (task_id, version)).fetchone()
            value = decode_body(db, row[0]) if row else None
        if not row:
            raise ContractError("任务或研究版本不存在")
        return value

    def list(self):
        with self.connect() as db:
            return [decode_body(db, row[0]) for row in db.execute("SELECT body FROM tasks ORDER BY rowid DESC")]

    def presentations(self, tasks):
        """UI metadata only; never alter research bodies or frozen deliveries."""
        keys = {(task.get('owner_account') or '', task.get('thread_id') or task['id']) for task in tasks}
        with self.connect() as db:
            rows = db.execute('SELECT owner_account,thread_id,body FROM task_presentations').fetchall()
        return {thread: json.loads(body) for owner, thread, body in rows if (owner, thread) in keys}

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
                members = [decode_body(db, row[0]) for row in db.execute('SELECT body FROM tasks')]
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

    def versions(self, task_id):
        with self.connect() as db:
            return [row[0] for row in db.execute("SELECT version FROM versions WHERE task_id=? ORDER BY version", (task_id,))]

    def delivery_versions(self, task_id):
        """List saved reports, not the checkpoints retained for cancellation/recovery."""
        with self.connect() as db:
            return [row[0] for row in db.execute(
                "SELECT version FROM deliveries WHERE task_id=? ORDER BY version", (task_id,))]

    def delivery_history(self, task_id):
        """Read report identities and their annotations from one archive snapshot.

        Checkpoints and another task's corrections never enter this list. Full
        report bodies are not decoded just to open the version selector.
        """
        with self.connect() as db:
            db.execute("BEGIN")
            versions = db.execute(
                "SELECT version,digest,created_at FROM deliveries WHERE task_id=? ORDER BY version DESC",
                (task_id,)).fetchall()
            notes = db.execute(
                "SELECT c.version,c.reason,c.created_at FROM corrections c "
                "JOIN deliveries d ON c.task_id=d.task_id AND c.version=d.version "
                "WHERE c.task_id=? ORDER BY c.rowid", (task_id,)).fetchall()
        by_version = {}
        for version, reason, created_at in notes:
            by_version.setdefault(version, []).append(dict(
                task_id=task_id, version=version, reason=reason, created_at=created_at))
        return [dict(task_id=task_id, version=version, digest=digest, created_at=created_at,
                     corrections=by_version.get(version, []))
                for version, digest, created_at in versions]

    def deliver(self, task, content, html):
        """State and all dependency snapshots commit in the same transaction."""
        from .reports import check_content, compose
        if not isinstance(content, dict):
            raise ContractError("交付内容须为规范报告对象，未冻结报告")
        check_content(content)
        if fingerprint(content) != fingerprint(compose(task)):
            raise ContractError("交付内容与当前研究依据不一致，未冻结报告")
        expected_html = task.get("pending_html")
        if expected_html is None and task.get("delivery"):
            # Completed tasks no longer carry a second copy of the paper.
            # An idempotent call must still match the verified frozen bytes.
            expected_html = self.report(task["id"], task["version"])["html"]
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
        digest = fingerprint(content)
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
            saved = dict(task, status="部分完成" if task["gaps"] else "已完成", revision=task["revision"] + 1,
                         delivery={"version": task["version"], "digest": digest,
                                   "html_sha256": hashlib.sha256(html.encode()).hexdigest(), "saved_at": now()})
            if content.get('report_kind') == 'research_draft':
                saved['delivery']['kind'] = 'research_draft'
            # Checkpoints remain durable on every failed transaction. Only a
            # successful freeze replaces them with the authoritative delivery.
            saved.pop("pending_content", None)
            saved.pop("pending_html", None)
            db.execute("INSERT INTO deliveries VALUES(?,?,?,?,?,?)",
                       (task["id"], task["version"], encode_body(db, content), html, digest, now()))
            body = encode_body(db, saved)
            db.execute("UPDATE tasks SET revision=?,body=? WHERE id=?", (saved["revision"], body, task["id"]))
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
            try:
                content = decode_body(db, row[0]) if row else None
                saved = decode_body(db, frozen[0]) if frozen else {}
            except (TypeError, ValueError, KeyError):
                raise ContractError("冻结报告记录格式无效，未返回报告") from None
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
            if (fingerprint(content) != row[2] or delivery.get("digest") != row[2]
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


def project_store(project, installation):
    project, installation = Path(project).resolve(), Path(installation).resolve()
    if project == installation or installation in project.parents:
        raise ContractError("Skill安装目录只读，研究项目必须独立")
    for folder in ("data", "result"):
        (project / folder).mkdir(parents=True, exist_ok=True)
    return ResearchArchive(project / "data" / "researchhelper")
