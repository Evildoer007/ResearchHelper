"""Actual DMG credential migration, role boundary and restart; offline only."""
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from unittest.mock import patch

from tests.accept_native_workbench_render import RenderRuntime

ROOT = Path(__file__).resolve().parents[1]
MODEL_SECRET = "RH_ACCEPTANCE_MODEL_FAKE_NEVER_REAL"
ACCESS = "RH_ACCEPTANCE_ACCESS_FAKE_NEVER_REAL"
REFRESH = "RH_ACCEPTANCE_REFRESH_FAKE_NEVER_REAL"
TINY = "RH_ACCEPTANCE_TINY_FAKE_NEVER_REAL"


class PrivateRuntime(RenderRuntime):
    def start(self):
        original = subprocess.Popen
        def guarded(args, *values, **options):
            if isinstance(args, list) and args and args[0] == "/usr/bin/sandbox-exec":
                args = list(args)
                for directory in ("/Applications/ResearchHelper.app", "/Users/haoranxu/Library/Application Support/ResearchHelper"):
                    args[2] += '(deny file-read* file-write* (subpath ' + json.dumps(directory) + '))'
                self.record["production_paths_denied"] = True
            return original(args, *values, **options)
        with patch.object(subprocess, "Popen", side_effect=guarded):
            return super().start()


def run():
    selected = os.environ.get("RESEARCHHELPER_CURRENT_RECEIPT")
    if not selected:
        raise RuntimeError("需要明确的当前候选")
    sys.path.insert(0, str(ROOT / "packaging"))
    from build_current import verify_current_build
    candidate = verify_current_build(selected, ROOT)
    folder = Path(tempfile.mkdtemp(prefix="packaged-credential-separation-", dir=ROOT / "result/acceptance"))
    store = folder / "store"
    store.mkdir(mode=0o700)
    model = {"endpoint": "https://example.invalid/chat/completions", "model": "offline-fixture", "api_key": MODEL_SECRET}
    sources = {"ifind": {"access_token": ACCESS, "refresh_token": REFRESH}, "tinyshare": {"token": TINY}}
    for name, value in (("model-settings.json", model), ("source-settings.json", sources)):
        file = store / name
        file.write_text(json.dumps(value))
        file.chmod(0o600)
    record = {"status": "failed", "scope": "actual_dmg_private_credentials_offline",
        "candidate": str(Path(selected).resolve()), "checks": [], "native_window_opened": False,
        "installed": False, "production_store_touched": False, "real_data_requests": 0,
        "model_calls": 0, "automatic_execution_enabled": False}
    runtime = PrivateRuntime(folder, record)
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        record["checks"].append(name)
    def local_client(base, account):
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        auth = json.loads(opener.open(base + "api/auth/status", timeout=10).read())
        token = auth["token"]
        def request(route, payload=None):
            headers = {"X-ResearchHelper-Token": token, "Content-Type": "application/json"}
            spec = urllib.request.Request(base + route, headers=headers,
                data=json.dumps(payload).encode() if payload is not None else None)
            try:
                with opener.open(spec, timeout=10) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                status = error.code
                error.close()
                return status, {}
        status, _ = request("api/auth/login", {"account": account, "password": "66666666" if account == "admin" else "88888888"})
        check(account + " login", status == 200)
        return request
    def secret(reference):
        material = (reference["provider"] + "\0" + reference["key"]).encode()
        target = store / "credentials" / (hashlib.sha256(material).hexdigest() + ".secret")
        check("referenced file private " + reference["key"].split("/")[1], target.stat().st_mode & 0o777 == 0o600)
        return target.read_text()
    try:
        base = runtime.start()
        saved = json.loads((store / "model-settings.json").read_text())
        source_saved = json.loads((store / "source-settings.json").read_text())
        check("legacy model migrated to reference", "api_key" not in saved and saved["secret_ref"]["provider"] == "local-secret")
        check("legacy source migrated to reference", source_saved["schema"] == "researchhelper.source-settings.v2")
        check("model value preserved", secret(saved["secret_ref"]) == MODEL_SECRET)
        for source, fields in sources.items():
            for name, value in fields.items():
                check(source + " " + name + " preserved", secret(source_saved[source][name]) == value)
        check("credential directory owner only", (store / "credentials").stat().st_mode & 0o777 == 0o700)
        admin = local_client(base, "admin")
        status, bootstrap = admin("api/bootstrap")
        check("admin bootstrap available", status == 200)
        check("production-style service remains offline", bootstrap["execution"]["mode"] == "offline")
        check("no task or research auto-started", not bootstrap["tasks"])
        check("public responses contain no credential values", all(value not in json.dumps(bootstrap) for value in (MODEL_SECRET, ACCESS, REFRESH, TINY)))
        original = (store / "model-settings.json").read_bytes()
        status, _ = admin("api/settings/model", dict(model, api_key=""))
        check("blank update preserves reference", status == 200 and original == (store / "model-settings.json").read_bytes())
        sales = local_client(base, "sales")
        status, sales_bootstrap = sales("api/bootstrap")
        check("sales cannot inspect settings", status == 200 and sales_bootstrap["model_settings"] == {} and sales_bootstrap["source_settings"] == {})
        status, _ = sales("api/settings/model", dict(model, api_key="REJECTED_FAKE"))
        check("sales cannot replace key", status == 403 and original == (store / "model-settings.json").read_bytes())
        # Remove one fabricated record: the updated host must still expose its
        # settings after restart, without silently saving blank replacements.
        reference = source_saved["tinyshare"]["token"]
        material = (reference["provider"] + "\0" + reference["key"]).encode()
        (store / "credentials" / (hashlib.sha256(material).hexdigest() + ".secret")).unlink()
        retained_config = (store / "source-settings.json").read_bytes()
        runtime.stop()
        runtime = PrivateRuntime(folder, record)
        base = runtime.start()
        admin = local_client(base, "admin")
        status, bootstrap = admin("api/bootstrap")
        check("restart with missing record keeps settings available", status == 200)
        check("missing credential reported without claiming ready", bootstrap["research_readiness"]["data_credentials_available"] is False)
        check("missing credential never silently blanks saved refs", (store / "source-settings.json").read_bytes() == retained_config)
        check("model ref and key survive restart", (store / "model-settings.json").read_bytes() == original and secret(saved["secret_ref"]) == MODEL_SECRET)
        status, _ = admin("api/settings/data", {"tinyshare": {"token": "REPAIRED_FAKE"}})
        check("explicit replacement repairs missing record", status == 200)
        status, bootstrap = admin("api/bootstrap")
        check("repair readiness updated", status == 200 and bootstrap["research_readiness"]["data_credentials_available"] is True)
        status, _ = admin("api/settings/model", dict(model, api_key="", clear_key=True))
        check("explicit model clear succeeds", status == 200)
        status, _ = admin("api/settings/data", {"ifind": {"clear_access_token": True, "clear_refresh_token": True}, "tinyshare": {"clear_token": True}})
        check("explicit source clear succeeds", status == 200)
        check("no superseded credential copies remain", not list((store / "credentials").glob("*.secret")))
        check("current candidate identity unchanged", verify_current_build(selected, ROOT) == candidate)
        record["status"] = "scoped_passed"
    except Exception as error:
        record["error"] = type(error).__name__
        if isinstance(error, AssertionError):
            record["failed_check"] = str(error)
    finally:
        try:
            runtime.stop()
        except Exception:
            record.update(status="failed", cleanup_error="runtime_cleanup_failed")
        (folder / "receipt.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({"status": record["status"], "checks": len(record["checks"]),
        "failed_check": record.get("failed_check"), "receipt": str(folder / "receipt.json")}, ensure_ascii=False))
    if record["status"] != "scoped_passed":
        raise SystemExit(1)


if __name__ == "__main__":
    run()
