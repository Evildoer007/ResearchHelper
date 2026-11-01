"""OH-style credential separation and migration, fabricated secrets only."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from researchhelper.credentials import LocalSecretStore
from researchhelper.model import ModelSettings
from researchhelper.settings import DataSettings


class LocalCredentialSeparation(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.model = ModelSettings(self.root)
        self.data = DataSettings(self.root)
        self.payload = {"endpoint": "https://example.invalid/chat/completions", "model": "fixture", "api_key": "MODEL_FIXTURE_ONLY"}
        self.sources = {"ifind": {"access_token": "ACCESS_FIXTURE_ONLY", "refresh_token": "REFRESH_FIXTURE_ONLY"}, "tinyshare": {"token": "TINY_FIXTURE_ONLY"}}

    def records(self):
        return list((self.root / "credentials").glob("*.secret"))

    def test_defaults_are_read_only_and_never_use_foreign_credentials(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "FOREIGN", "IFIND_ACCESS_TOKEN": "FOREIGN"}):
            self.assertFalse(self.model.read()["has_key"])
            self.assertFalse(self.data.read()["ifind"]["has_access_token"])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_model_configuration_contains_only_reference_and_private_resolve_works(self):
        self.model.save(self.payload)
        saved = json.loads(self.model.path.read_text())
        self.assertNotIn("api_key", saved)
        self.assertNotIn(self.payload["api_key"], self.model.path.read_text())
        self.assertEqual(saved["secret_ref"]["provider"], "local-secret")
        self.assertEqual(self.model.read(private=True)["api_key"], self.payload["api_key"])

    def test_source_configuration_contains_only_purpose_bound_references(self):
        self.data.save(self.sources)
        raw = self.data.path.read_text()
        self.assertEqual(json.loads(raw)["schema"], DataSettings.schema)
        for fields in self.sources.values():
            for value in fields.values():
                self.assertNotIn(value, raw)
        self.assertEqual(self.data.read(private=True), self.sources)

    def test_public_reads_never_resolve_secret_values(self):
        self.model.save(self.payload)
        self.data.save(self.sources)
        with patch.object(LocalSecretStore, "resolve", side_effect=AssertionError("must not read")):
            self.assertTrue(self.model.read()["has_key"])
            self.assertTrue(self.data.read()["ifind"]["has_refresh_token"])

    def test_all_secret_files_and_settings_are_private(self):
        self.model.save(self.payload)
        self.data.save(self.sources)
        for file in self.records() + [self.model.path, self.data.path]:
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / "credentials").stat().st_mode & 0o777, 0o700)

    def test_blank_save_is_idempotent_and_preserves_key(self):
        self.model.save(self.payload)
        original = self.model.path.read_bytes()
        records = self.records()
        self.model.save(dict(self.payload, api_key=""))
        self.assertEqual(self.model.path.read_bytes(), original)
        self.assertEqual(self.records(), records)

    def test_clear_removes_only_exact_owned_reference(self):
        self.model.save(self.payload)
        self.data.save(self.sources)
        self.model.save(dict(self.payload, api_key="NEW_IGNORED", clear_key=True))
        self.assertFalse(self.model.read()["has_key"])
        self.assertEqual(self.model.read(private=True)["api_key"], "")
        self.assertEqual(len(self.records()), 3)
        self.assertEqual(self.data.read(private=True), self.sources)

    def test_replacement_removes_old_record_after_commit(self):
        self.model.save(self.payload)
        old = self.records()[0]
        self.model.save(dict(self.payload, api_key="NEW_FIXTURE_ONLY"))
        self.assertFalse(old.exists())
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(self.model.read(private=True)["api_key"], "NEW_FIXTURE_ONLY")

    def test_failed_config_commit_preserves_old_key_and_cleans_new_record(self):
        self.model.save(self.payload)
        original = self.model.path.read_bytes()
        before = {p.name: p.read_bytes() for p in self.records()}
        with patch("researchhelper.credentials.commit_configuration", side_effect=OSError("disk failure")), self.assertRaises(OSError):
            self.model.save(dict(self.payload, api_key="NEW_FIXTURE_ONLY"))
        self.assertEqual(self.model.path.read_bytes(), original)
        self.assertEqual({p.name: p.read_bytes() for p in self.records()}, before)
        self.assertEqual(self.model.read(private=True)["api_key"], self.payload["api_key"])

    def test_failed_multifield_source_commit_keeps_all_previous_values(self):
        self.data.save(self.sources)
        raw = self.data.path.read_bytes()
        before = {p.name: p.read_bytes() for p in self.records()}
        with patch("researchhelper.credentials.commit_configuration", side_effect=OSError("disk failure")), self.assertRaises(OSError):
            self.data.save({"ifind": {"access_token": "NEW_ACCESS", "refresh_token": "NEW_REFRESH"}})
        self.assertEqual(self.data.path.read_bytes(), raw)
        self.assertEqual({p.name: p.read_bytes() for p in self.records()}, before)

    def test_legacy_model_read_does_not_migrate_until_updated_host_requests_it(self):
        self.model.path.write_text(json.dumps(self.payload))
        original = self.model.path.read_bytes()
        self.assertEqual(self.model.read(private=True), self.payload)
        self.assertEqual(self.model.path.read_bytes(), original)
        self.assertFalse((self.root / "credentials").exists())
        self.assertTrue(self.model.migrate_legacy())
        self.assertEqual(self.model.read(private=True), self.payload)
        self.assertNotIn("api_key", json.loads(self.model.path.read_text()))
        self.assertFalse(self.model.migrate_legacy())

    def test_legacy_sources_migrate_without_changing_values(self):
        self.data.path.write_text(json.dumps(self.sources))
        before = self.data.path.read_bytes()
        self.assertEqual(self.data.read(private=True), self.sources)
        self.assertEqual(self.data.path.read_bytes(), before)
        self.assertTrue(self.data.migrate_legacy())
        self.assertEqual(self.data.read(private=True), self.sources)
        self.assertFalse(self.data.migrate_legacy())

    def test_failed_migration_preserves_original_and_does_not_leave_secret_copy(self):
        self.model.path.write_text(json.dumps(self.payload))
        before = self.model.path.read_bytes()
        with patch("researchhelper.credentials.commit_configuration", side_effect=OSError("disk")), self.assertRaises(OSError):
            self.model.migrate_legacy()
        self.assertEqual(self.model.path.read_bytes(), before)
        self.assertEqual(self.records(), [])

    def test_missing_record_fails_closed_but_explicit_replacement_repairs_it(self):
        self.model.save(self.payload)
        self.records()[0].unlink()
        with self.assertRaises(ContractError):
            self.model.read(private=True)
        self.model.save(dict(self.payload, api_key="REPAIRED_FIXTURE"))
        self.assertEqual(self.model.read(private=True)["api_key"], "REPAIRED_FIXTURE")

    def test_missing_source_record_can_be_explicitly_replaced(self):
        self.data.save(self.sources)
        ref = json.loads(self.data.path.read_text())["tinyshare"]["token"]
        self.data.secrets.path(ref, "tinyshare-token").unlink()
        self.data.save({"tinyshare": {"token": "REPAIRED_FIXTURE"}})
        self.assertEqual(self.data.read(private=True)["tinyshare"]["token"], "REPAIRED_FIXTURE")

    def test_cross_purpose_and_foreign_product_references_rejected(self):
        self.model.save(self.payload)
        self.data.save(self.sources)
        saved = json.loads(self.model.path.read_text())
        saved["secret_ref"] = json.loads(self.data.path.read_text())["tinyshare"]["token"]
        self.model.path.write_text(json.dumps(saved))
        with self.assertRaises(ContractError):
            self.model.read(private=True)
        saved["secret_ref"] = {"provider": "local-secret", "key": "optionhelper/model/" + "a" * 32}
        self.model.path.write_text(json.dumps(saved))
        with self.assertRaises(ContractError):
            self.model.read()

    def test_traversal_reference_rejected_without_reading_target(self):
        self.model.path.write_text(json.dumps({"endpoint": self.payload["endpoint"], "model": "fixture",
            "secret_ref": {"provider": "local-secret", "key": "../../outside"}}))
        with self.assertRaises(ContractError):
            self.model.save(self.payload)

    def test_linked_configuration_rejected_without_changing_target(self):
        outside = self.root / "outside.json"
        outside.write_text(json.dumps(self.payload))
        self.model.path.symlink_to(outside)
        with self.assertRaises(ContractError):
            self.model.save(self.payload)
        self.assertEqual(json.loads(outside.read_text()), self.payload)

    def test_linked_credential_directory_rejected_without_copying_values(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "credentials").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ContractError):
            self.model.save(self.payload)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse(self.model.path.exists())

    def test_linked_or_public_secret_files_not_resolved(self):
        self.model.save(self.payload)
        file = self.records()[0]
        file.chmod(0o644)
        with self.assertRaises(ContractError):
            self.model.read(private=True)
        file.chmod(0o600)
        outside = self.root / "outside.secret"
        file.rename(outside)
        file.symlink_to(outside)
        with self.assertRaises(ContractError):
            self.model.read(private=True)

    def test_partial_source_updates_preserve_untouched_references(self):
        self.data.save(self.sources)
        before = json.loads(self.data.path.read_text())
        self.data.save({"tinyshare": {"clear_token": True}})
        after = json.loads(self.data.path.read_text())
        self.assertEqual(before["ifind"], after["ifind"])
        self.assertIsNone(after["tinyshare"]["token"])

    def test_no_automatic_execution_change_or_archive_writes(self):
        self.model.save(self.payload)
        self.data.save(self.sources)
        self.assertEqual({p.name for p in self.root.iterdir()}, {"model-settings.json", "source-settings.json", "credentials"})

    def test_corrupt_legacy_is_not_overwritten(self):
        self.model.path.write_text('{"api_key":')
        before = self.model.path.read_bytes()
        with self.assertRaises(ContractError):
            self.model.save(self.payload)
        self.assertEqual(self.model.path.read_bytes(), before)
        self.assertEqual(self.records(), [])

    def test_actual_atomic_replace_failure_keeps_working_credentials(self):
        self.data.save(self.sources)
        raw = self.data.path.read_bytes()
        before = {p.name: p.read_bytes() for p in self.records()}
        with patch("researchhelper.credentials.os.replace", side_effect=OSError("replace failed")), self.assertRaises(OSError):
            self.data.save({"tinyshare": {"token": "NEW_FIXTURE_ONLY"}})
        self.assertEqual(self.data.path.read_bytes(), raw)
        self.assertEqual({p.name: p.read_bytes() for p in self.records()}, before)
        self.assertFalse(list(self.root.glob(".source-settings-*")))

    def test_concurrent_external_config_change_is_preserved(self):
        self.model.save(self.payload)
        original_create = self.model.secrets.create
        concurrent = dict(self.payload, model="user-concurrent-choice")
        def change(value, purpose):
            ref = original_create(value, purpose)
            self.model.path.write_text(json.dumps(concurrent))
            return ref
        with patch.object(self.model.secrets, "create", side_effect=change), self.assertRaises(ContractError):
            self.model.save(dict(self.payload, api_key="NEW_FIXTURE_ONLY"))
        self.assertEqual(json.loads(self.model.path.read_text()), concurrent)
        self.assertEqual(len(self.records()), 1)  # only the previously committed record

    def test_locked_offline_host_does_not_migrate_legacy_files(self):
        from researchhelper.service import make_server
        root = Path(__file__).resolve().parents[2]
        self.model.path.write_text(json.dumps(self.payload))
        self.data.path.write_text(json.dumps(self.sources))
        before = (self.model.path.read_bytes(), self.data.path.read_bytes())
        server = make_server(self.root, root, offline=True)
        try:
            self.assertEqual((self.model.path.read_bytes(), self.data.path.read_bytes()), before)
            self.assertFalse((self.root / "credentials").exists())
        finally:
            server.server_close()

    def test_updated_native_host_migrates_only_its_own_legacy_files(self):
        from researchhelper.service import make_server
        from researchhelper.runtime import ExecutionPolicy
        root = Path(__file__).resolve().parents[2]
        self.model.path.write_text(json.dumps(self.payload))
        self.data.path.write_text(json.dumps(self.sources))
        server = make_server(self.root, root, offline=True, authentication=True,
                             runtime_configurable=True, preset_accounts=True)
        try:
            self.assertNotIn("api_key", json.loads(self.model.path.read_text()))
            self.assertEqual(self.model.read(private=True), self.payload)
            self.assertEqual(self.data.read(private=True), self.sources)
            self.assertFalse(ExecutionPolicy(self.root).read()["enabled"])
        finally:
            server.server_close()

    def test_native_host_can_start_with_missing_source_record_without_blank_save(self):
        from researchhelper.service import make_server
        root = Path(__file__).resolve().parents[2]
        self.data.save(self.sources)
        saved = self.data.path.read_bytes()
        ref = json.loads(saved)["tinyshare"]["token"]
        self.data.secrets.path(ref, "tinyshare-token").unlink()
        server = make_server(self.root, root, offline=True, authentication=True,
                             runtime_configurable=True, preset_accounts=True)
        try:
            self.assertEqual(self.data.path.read_bytes(), saved)
        finally:
            server.server_close()
        self.data.save({"tinyshare": {"token": "RESTORED_FIXTURE"}})
        self.assertEqual(self.data.read(private=True)["tinyshare"]["token"], "RESTORED_FIXTURE")


if __name__ == "__main__":
    unittest.main()
