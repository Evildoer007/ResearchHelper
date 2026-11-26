"""ResearchHelper-owned source credentials, separate from task archives and demos."""
from __future__ import annotations

import json
from pathlib import Path

from .contracts import ContractError, unique_json_object
from .credentials import LocalSecretStore, configuration_lock, owned_bytes, replace_references


class DataSettings:
    fields = {"ifind": ("access_token", "refresh_token"), "tinyshare": ("token",)}

    def __init__(self, store):
        self.path = Path(store) / "source-settings.json"
        self.lock = configuration_lock(self.path)
        self.secrets = LocalSecretStore(store)

    schema = "researchhelper.source-settings.v2"

    @staticmethod
    def _credential(value):
        if not isinstance(value, str) or len(value) > 16384:
            raise ContractError("数据源凭据须为不含换行的文字，最长16384字")
        value = value.strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ContractError("数据源凭据不能包含换行或控制字符")
        return value

    def _configuration(self):
        raw = owned_bytes(self.path)
        if raw is None:
            return {source: {name: "" for name in names} for source, names in self.fields.items()}
        try:
            saved = json.loads(raw, object_pairs_hook=unique_json_object)
        except (ValueError, RecursionError):
            raise ContractError("数据源配置JSON损坏或含重复字段，原件保留") from None
        if not isinstance(saved, dict):
            raise ContractError("数据源配置字段无效")
        references = "schema" in saved
        if set(saved) != set(self.fields) | ({"schema"} if references else set()) or references and saved["schema"] != self.schema:
            raise ContractError("数据源配置版本无效")
        for source, names in self.fields.items():
            if not isinstance(saved[source], dict) or set(saved[source]) != set(names):
                raise ContractError("数据源配置字段无效")
            for name, value in saved[source].items():
                if references:
                    if value is not None:
                        self.secrets.validate(value, source + "-" + name)
                else:
                    self._credential(value)
        return saved

    def read(self, private=False):
        with self.lock:
            try:
                saved = self._configuration()
                references = "schema" in saved
                if private:
                    return {source: {name: (self.secrets.resolve(saved[source][name], source + "-" + name)
                        if saved[source][name] else "") if references else self._credential(saved[source][name])
                        for name in names} for source, names in self.fields.items()}
                return {source: {"has_" + name: bool(saved[source][name]) for name in names}
                    for source, names in self.fields.items()}
            except (OSError, ValueError, TypeError, ContractError):
                raise ContractError("ResearchHelper数据源配置无法读取，请恢复原配置或重新保存凭据；不会覆盖") from None

    def migrate_legacy(self):
        with self.lock:
            if not self.path.exists() or "schema" in self._configuration():
                return False
            legacy = self.read(private=True)
            self.save(legacy)
            return True

    def save(self, payload):
        if not isinstance(payload, dict) or set(payload) - set(self.fields):
            raise ContractError("仅支持独立iFinD与Tinyshare配置")
        changes = {}
        for source, incoming in payload.items():
            names = self.fields[source]
            if not isinstance(incoming, dict) or set(incoming) - set(names) - {"clear_" + name for name in names}:
                raise ContractError("数据源配置字段无效")
            changes[source] = {}
            for name in names:
                clear = incoming.get("clear_" + name, False)
                if not isinstance(clear, bool):
                    raise ContractError("清除凭据必须使用明确布尔选项")
                value = self._credential(incoming.get(name, ""))
                if clear or value:
                    changes[source][name] = "" if clear else value
        with self.lock:
            expected = owned_bytes(self.path)
            try:
                persisted = self._configuration()
            except (OSError, ValueError, TypeError, ContractError):
                raise ContractError("ResearchHelper数据源配置无法读取，请恢复原配置；不会覆盖") from None
            references = "schema" in persisted
            values, old_references = {}, {}
            for source, names in self.fields.items():
                for name in names:
                    purpose = source + "-" + name
                    prior = persisted[source][name]
                    old_references[purpose] = prior if references else None
                    if name in changes.get(source, {}):
                        values[purpose] = changes[source][name]
                    else:
                        values[purpose] = (self.secrets.resolve(prior, purpose) if prior else "") if references else self._credential(prior)
            replace_references(self.path, self.secrets,
                lambda refs: dict({source: {name: refs[source + "-" + name] for name in names}
                    for source, names in self.fields.items()}, schema=self.schema),
                values, old_references, expected)
            return self.read()
