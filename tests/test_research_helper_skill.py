from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "research-helper"
ENTRY = SKILL / "scripts" / "tool_entry.py"


def _run(*args: str, input_text: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ENTRY), *args],
        input=input_text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=ROOT,
        timeout=60,
        check=False,
    )


def _load_entry():
    spec = importlib.util.spec_from_file_location("research_helper_skill_entry", ENTRY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_skill_metadata_and_entrypoint_exist():
    assert (SKILL / "README.md").is_file()
    assert (SKILL / "SKILL.md").is_file()
    assert (SKILL / "agents" / "openai.yaml").is_file()
    assert ENTRY.is_file()


def test_tool_list_is_machine_readable():
    result = _run("--list")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["skill"] == "research-helper"
    assert payload["version"] == "0.3.1"
    assert "run-research" in payload["actions"]
    assert "prepare-viewpoints" in payload["actions"]


def test_runtime_resolves_to_current_project():
    module = _load_entry()
    assert module._runtime_root() == ROOT


def test_windows_data_root_ignores_codex_localappdata_sandbox(monkeypatch):
    module = _load_entry()
    user = Path("C:/Users/example-user")
    runtime = Path("C:/portable-skill/runtime_source")
    monkeypatch.delenv("RESEARCH_HELPER_DATA_ROOT", raising=False)
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("LOCALAPPDATA", str(user / "AppData" / "Local" / "Packages" / "sandbox" / "AC"))
    monkeypatch.setattr(module, "_runtime_root", lambda: runtime)
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    assert module._platform_data_root() == (user / "AppData" / "Local" / "ResearchHelper").resolve()


def test_invalid_action_returns_json_error():
    result = _run("unknown-action", input_text="{}")
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "invalid_action"


def test_run_research_requires_explicit_viewpoint_selection():
    result = _run("run-research", input_text=json.dumps({
        "request": "未来一个月机器人行业的投资机会",
        "market_confirmation": {"market": "A股"},
    }, ensure_ascii=False))
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "needs_input"
    assert payload["required_type"] == "viewpoint_selection"
    assert payload["next_action"] == "prepare-viewpoints"
    assert "候选观点" in payload["next_prompt"]


def test_logic_pick_ids_are_resolved_against_current_candidates():
    module = _load_entry()
    marker = {
        "candidates": [
            {"index": 1, "id": "V1"},
            {"index": 2, "id": "event_chain_1"},
            {"index": 3, "id": "doc_1"},
        ]
    }
    assert module._logic_pick_reply(marker, {
        "logic_pick_ids": ["event_chain_1", "V1"],
    }) == "1 2"
    assert module._logic_pick_reply(marker, {
        "logic_pick_ids": ["missing"],
    }) is None


def test_skill_requires_guided_checkpoints_and_native_template():
    content = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "Guided conversation contract" in content
    assert "prepare-viewpoints" in content
    assert "Never recreate, redesign, or replace" in content
    assert "successful native PDF export is not blocked by page count" in content
    assert "successful one-page check" not in content

