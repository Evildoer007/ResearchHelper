"""Portable task backups with one copy of each large value or source file."""

import base64
import codecs
import hashlib
import json
import re
import stat
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile
from zlib import error as CompressionError

from .contracts import ContractError, unique_json_object

PACKAGE_LIMIT = 128 * 1024 * 1024
EXPANDED_LIMIT = 384 * 1024 * 1024
MANIFEST_LIMIT = 64 * 1024 * 1024
ORIGINAL_LIMIT = 25 * 1024 * 1024
FILE_INDEX_VERSION = 2
MANIFEST = "researchhelper.json"
FORMAT = "ResearchHelperTaskBackup"
REF = "$researchhelper_file"
LITERAL = "$researchhelper_literal"
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
EXTENSIONS = {"pdf", "docx", "txt", "md", "html", "htm"}
MATERIAL_GROUPS = ("materials", "pending_materials", "source_records", "pending_source_records")


class BackupPackageRequired(ContractError):
    code = "backup_package_required"


def limited_json(value, limit):
    """Stop at the JSON bridge limit before allocating an expanded copy."""
    output = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    for part in encoder.iterencode(value):
        raw = part.encode()
        if len(output) + len(raw) > limit:
            raise BackupPackageRequired("任务备份超过20MB，需使用ZIP备份。")
        output.extend(raw)
    return bytes(output)


def _version(value):
    return type(value) is int and 0 < value <= 2**53 - 1


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _literal_record(value):
    if isinstance(value, dict) and set(value) == {LITERAL}:
        return value[LITERAL]
    return value


def _embedded_material(task, group, index):
    if group not in MATERIAL_GROUPS or type(index) is not int or index < 0:
        return None
    records = task.get(group)
    if not isinstance(records, list) or index >= len(records):
        return None
    record = _literal_record(records[index])
    if (not isinstance(record, dict) or record.get("task_id") != task["id"]
            or not _version(record.get("research_version")) or record["research_version"] > task["version"]):
        return None
    version = record.get("retained_from_version", record["research_version"])
    if not _version(version) or version > record["research_version"]:
        return None
    snapshot = _literal_record(record.get("snapshot", record))
    if record.get("kind") == "material":
        material = snapshot
    elif record.get("kind") == "filing" and isinstance(snapshot, dict):
        material = _literal_record(snapshot.get("material"))
    else:
        return None
    return (record, material, version) if isinstance(material, dict) else None


def embedded_materials(task):
    """Find originals bound to the task and an existing research version."""
    for group in MATERIAL_GROUPS:
        records = task.get(group)
        for index in range(len(records) if isinstance(records, list) else 0):
            if (bound := _embedded_material(task, group, index)) is not None:
                yield group, index, *bound


def embedded_original_bytes(wire, sha256, byte_count=None):
    if not isinstance(wire, str) or len(wire) > (ORIGINAL_LIMIT + 2) // 3 * 4:
        raise ContractError("留存原件字节格式无效")
    try:
        raw = base64.b64decode(wire, validate=True)
    except ValueError:
        raise ContractError("留存原件字节格式无效") from None
    if len(raw) > ORIGINAL_LIMIT or (byte_count is not None and len(raw) != byte_count) or _digest(raw) != sha256:
        raise ContractError("留存原件完整性不一致")
    return raw


def _original_name(name):
    if not isinstance(name, str):
        raise ContractError("备份原件文件名无效")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in name:
        raise ContractError("备份原件文件名无效")
    stem, suffix = name.rsplit(".", 1)
    suffix = suffix.lower()
    if suffix not in EXTENSIONS:
        raise ContractError("备份原件类型无效")
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f-\x9f]', "_", stem).strip()[:54].rstrip(" .") or "原件"
    if re.match(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", stem):
        stem = "_" + stem
    return stem + "." + suffix


def write_package(backup, target):
    """Write the task, frozen reports and originals to a seekable ZIP."""
    objects, references = {}, {}
    expanded = 0
    with ZipFile(target, "w", compression=ZIP_DEFLATED, compresslevel=3) as archive:
        def put(raw, path=None):
            nonlocal expanded
            digest = _digest(raw)
            if digest not in objects:
                expanded += len(raw)
                if expanded > EXPANDED_LIMIT:
                    raise ContractError("任务内容超过ZIP备份容量")
                path = path or "data/" + digest
                archive.writestr(path, raw)
                objects[digest] = {"path": path, "byte_count": len(raw)}
            return digest

        def reference(value, codec, path=None):
            key = (codec, value)
            if key not in references:
                raw = value.encode() if codec == "utf8" else base64.b64decode(value, validate=True)
                references[key] = {REF: put(raw, path), "codec": codec}
            return references[key]

        for report in backup["reports"]:
            reference(report["html"], "utf8", f'reports/version-{report["version"]}.html')
        for index, original in enumerate(backup["originals"], 1):
            name = _original_name(original["name"])
            reference(original["original_base64"], "base64", f"originals/{index:04d}-{name}")

        def compact(value, key=None):
            if isinstance(value, dict):
                item = {field: compact(child, field) for field, child in value.items()}
                return {LITERAL: item} if REF in value or LITERAL in value else item
            if isinstance(value, list):
                return [compact(child) for child in value]
            if isinstance(value, str):
                codec = "base64" if key == "original_base64" else "utf8"
                match = references.get((codec, value))
                if match is not None:
                    return match
                if len(value) >= 8192:
                    if codec == "base64":
                        # Older snapshots can contain diagnostic strings in
                        # this field. Preserve their exact text representation.
                        try:
                            raw = base64.b64decode(value, validate=True)
                            if base64.b64encode(raw).decode("ascii") == value:
                                references[(codec, value)] = {REF: put(raw), "codec": codec}
                                return references[(codec, value)]
                        except ValueError:
                            pass
                    return reference(value, "utf8")
            return value

        manifest = {"format": FORMAT, "version": 1, "objects": objects, "backup": compact(backup)}
        metadata = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        if len(metadata) > MANIFEST_LIMIT or len(metadata) + expanded > EXPANDED_LIMIT:
            raise ContractError("任务内容超过ZIP备份容量")
        archive.writestr(MANIFEST, metadata)
    size = target.tell()
    if not 0 < size <= PACKAGE_LIMIT:
        raise ContractError("任务ZIP备份超过128MiB")
    target.seek(0)
    return size


def _safe_member(name):
    path = PurePosixPath(name)
    if path.as_posix() != name or path.is_absolute() or len(path.parts) != 2:
        return False
    folder, member = path.parts
    if folder == "data":
        return bool(DIGEST.fullmatch(member))
    if folder == "reports":
        return bool(re.fullmatch(r"version-[1-9][0-9]*\.html", member))
    match = re.fullmatch(r"[0-9]{4,}-(.+)", member)
    return bool(folder == "originals" and match and _original_name(match[1]) == match[1])


def _read_package(payload, *, expand=False, index_only=False):
    if not 0 < len(payload) <= PACKAGE_LIMIT:
        raise ContractError("任务ZIP备份超过128MiB")
    try:
        with ZipFile(BytesIO(payload)) as archive:
            members = archive.infolist()
            by_name = {member.filename: member for member in members}
            if (len(by_name) != len(members) or MANIFEST not in by_name or by_name[MANIFEST].file_size > MANIFEST_LIMIT
                    or sum(member.file_size for member in members) > EXPANDED_LIMIT
                    or any(member.is_dir() or member.flag_bits & 1
                           or stat.S_IFMT(member.external_attr >> 16) not in (0, stat.S_IFREG)
                           or member.compress_type not in (ZIP_DEFLATED, ZIP_STORED)
                           or member.filename != MANIFEST and not _safe_member(member.filename) for member in members)):
                raise ContractError("任务ZIP备份目录或展开体积无效")
            manifest = json.loads(archive.read(MANIFEST).decode("utf-8"), object_pairs_hook=unique_json_object)
            if (not isinstance(manifest, dict) or manifest.get("format") != FORMAT
                    or type(manifest.get("version")) is not int or manifest["version"] != 1
                    or not isinstance(manifest.get("objects"), dict) or not isinstance(manifest.get("backup"), dict)):
                raise ContractError("请选择ResearchHelper生成的任务ZIP备份")
            objects, backup = manifest["objects"], manifest["backup"]
            used = {MANIFEST}
            encoding = {}
            for digest, item in objects.items():
                if (not DIGEST.fullmatch(digest) or not isinstance(item, dict) or set(item) != {"path", "byte_count"}
                        or not isinstance(item["path"], str) or item["path"] in used or item["path"] not in by_name
                        or type(item["byte_count"]) is not int or item["byte_count"] != by_name[item["path"]].file_size):
                    raise ContractError("任务ZIP备份文件索引无效")
                used.add(item["path"])
            if used != set(by_name):
                raise ContractError("任务ZIP备份含未登记文件")

            def object_for(value, codec=None):
                if (not isinstance(value, dict) or set(value) != {REF, "codec"}
                        or not isinstance(value.get(REF), str) or value[REF] not in objects
                        or value["codec"] not in ("utf8", "base64") or codec and value["codec"] != codec):
                    raise ContractError("任务ZIP备份文件引用无效")
                encoding.setdefault(value[REF], set()).add(value["codec"])
                return objects[value[REF]]

            restored_values = {}

            def value_text(value, item):
                key = (value[REF], value["codec"])
                if key not in restored_values:
                    raw = archive.read(item["path"])
                    restored_values[key] = raw.decode("utf-8") if key[1] == "utf8" else base64.b64encode(raw).decode("ascii")
                return restored_values[key]

            def walk(value, *, literal=False):
                if isinstance(value, dict):
                    if not literal and REF in value:
                        item = object_for(value)
                        return value_text(value, item) if expand else value
                    if not literal and LITERAL in value:
                        if set(value) != {LITERAL} or not isinstance(value[LITERAL], dict):
                            raise ContractError("任务ZIP备份字面对象无效")
                        return walk(value[LITERAL], literal=True)
                    return {key: walk(child) for key, child in value.items()}
                if isinstance(value, list):
                    return [walk(child) for child in value]
                if isinstance(value, float) and not float("-inf") < value < float("inf"):
                    raise ContractError("任务ZIP备份包含无效数值")
                return value

            checked = walk(backup)
            if encoding.keys() != objects.keys():
                raise ContractError("任务ZIP备份含未关联文件")
            if not index_only:
                for digest, item in objects.items():
                    actual = hashlib.sha256()
                    decoder = codecs.getincrementaldecoder("utf-8")() if "utf8" in encoding[digest] else None
                    with archive.open(item["path"]) as source:
                        for chunk in iter(lambda: source.read(128 * 1024), b""):
                            actual.update(chunk)
                            if decoder:
                                decoder.decode(chunk)
                    if decoder:
                        decoder.decode(b"", final=True)
                    if actual.hexdigest() != digest:
                        raise ContractError("任务ZIP备份文件指纹不一致")
            tasks = backup.get("tasks")
            if (backup.get("workspaceVersion") != 4 or not isinstance(tasks, list) or len(tasks) != 1
                    or not isinstance(tasks[0], dict) or not isinstance(tasks[0].get("id"), str)
                    or not _version(tasks[0].get("version")) or not isinstance(backup.get("reports"), list)
                    or not isinstance(backup.get("originals"), list)):
                raise ContractError("任务ZIP备份缺少研究任务")
            task = tasks[0]
            title = task.get("question")
            if isinstance(title, dict):
                title = value_text(title, object_for(title, "utf8"))
            if not isinstance(title, str) or not title.strip() or len(title) > 20_000:
                raise ContractError("任务ZIP备份的研究问题无效")
            files = []
            report_versions = set()
            for report in backup["reports"]:
                version = report.get("version") if isinstance(report, dict) else None
                content = report.get("content") if isinstance(report, dict) else None
                if (not _version(version) or version > task["version"] or version in report_versions
                        or not isinstance(content, dict) or content.get("task_id") != task["id"]
                        or content.get("research_version") != version):
                    raise ContractError("任务ZIP备份报告版本不一致")
                report_versions.add(version)
                ref = report.get("html")
                item = object_for(ref, "utf8")
                if ref[REF] != report.get("html_sha256"):
                    raise ContractError("任务ZIP备份报告指纹不一致")
                files.append(dict(item, kind="report", version=version, sha256=ref[REF], filename=f"研究报告_v{version}.html"))
            known_originals = set()
            for original in backup["originals"]:
                version = original.get("research_version") if isinstance(original, dict) else None
                if (not _version(version) or version > task["version"] or original.get("task_id") != task["id"]):
                    raise ContractError("任务ZIP备份原件版本不一致")
                ref = original.get("original_base64")
                item = object_for(ref, "base64")
                if (ref[REF] != original.get("sha256") or item["byte_count"] != original.get("byte_count")
                        or item["byte_count"] > ORIGINAL_LIMIT):
                    raise ContractError("任务ZIP备份原件指纹或体积不一致")
                files.append(dict(item, kind="original", version=version, sha256=ref[REF],
                                  filename=_original_name(original.get("name"))))
                known_originals.add((version, original["name"], ref[REF]))
            # Earlier packages can store a file only in the checkpoint. Keep
            # the existing file positions and append verified missing entries.
            for group, index, _, material, version in embedded_materials(task):
                name, digest = material.get("name"), material.get("hash")
                if not isinstance(name, str) or not isinstance(digest, str) or not DIGEST.fullmatch(digest):
                    continue
                key = (version, name, digest)
                if key in known_originals:
                    continue
                wire = material.get("original_base64")
                try:
                    filename = _original_name(name)
                    if isinstance(wire, dict):
                        item = object_for(wire, "base64")
                        if wire[REF] != digest or item["byte_count"] > ORIGINAL_LIMIT:
                            continue
                    else:
                        raw = embedded_original_bytes(wire, digest)
                        item = {"embedded_source": [group, index], "byte_count": len(raw)}
                except ContractError:
                    continue
                files.append(dict(item, kind="original", version=version, sha256=digest, filename=filename))
                known_originals.add(key)
            if index_only:
                return files
            receipt = {"title": title.strip(), "files": files, "sha256": _digest(payload), "byte_count": len(payload),
                       "file_index_version": FILE_INDEX_VERSION}
            return (receipt, checked) if expand else receipt
    except (BadZipFile, CompressionError, KeyError, ValueError, TypeError, RuntimeError) as error:
        if isinstance(error, ContractError):
            raise
        raise ContractError("任务ZIP备份无法读取或内容损坏") from None


def inspect_package(payload, *, expand=False):
    """Verify an incoming backup before it can be stored."""
    return _read_package(payload, expand=expand)


def read_saved_package_file_index(payload):
    """Read the index after the archive matches the bytes to its import receipt."""
    return _read_package(payload, index_only=True)


def read_package_file(payload, entry):
    try:
        with ZipFile(BytesIO(payload)) as archive:
            if "embedded_source" in entry:
                slot = entry["embedded_source"]
                if not isinstance(slot, list) or len(slot) != 2:
                    raise ContractError("备份原件的位置已失效")
                manifest = json.loads(archive.read(MANIFEST).decode("utf-8"), object_pairs_hook=unique_json_object)
                task = manifest["backup"]["tasks"][0]
                group, index = slot
                bound = _embedded_material(task, group, index)
                if bound is None:
                    raise ContractError("备份原件的位置已失效")
                _, material, version = bound
                if (entry["kind"] != "original" or version != entry["version"]
                        or _original_name(material.get("name")) != entry["filename"]
                        or material.get("hash") != entry["sha256"]):
                    raise ContractError("备份原件身份不一致")
                return embedded_original_bytes(material.get("original_base64"), entry["sha256"], entry["byte_count"])
            if archive.getinfo(entry["path"]).file_size != entry["byte_count"]:
                raise ContractError("备份文件长度校验失败")
            data = archive.read(entry["path"])
    except (BadZipFile, CompressionError, KeyError, RuntimeError):
        raise ContractError("备份文件内容损坏") from None
    if len(data) != entry["byte_count"] or _digest(data) != entry["sha256"]:
        raise ContractError("备份文件指纹校验失败")
    return data
