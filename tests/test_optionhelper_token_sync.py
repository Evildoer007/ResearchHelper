from __future__ import annotations

from pathlib import Path

from core.optionhelper_token_sync import sync_optionhelper_refresh_token


def _fake_skill(root: Path) -> Path:
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "environment_check.py").write_text(
        """
from pathlib import Path

def _path(project_root):
    return Path(project_root) / '.optionhelper' / 'memory.md'

def load_ifind_refresh_token(*, project_root=None, skill_root=None):
    path = _path(project_root)
    if not path.exists():
        return None
    return path.read_text(encoding='utf-8').split(': ', 1)[1].strip()

def save_ifind_refresh_token(token, project_root=None, *, skill_root=None):
    path = _path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('token: ' + token, encoding='utf-8')
    return path
""".strip(),
        encoding="utf-8",
    )
    return root


def test_stale_optionhelper_token_is_replaced_without_exposing_secret(tmp_path):
    skill = _fake_skill(tmp_path / "skill")
    project = tmp_path / "project"
    memory = project / ".optionhelper" / "memory.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("token: stale-secret", encoding="utf-8")

    result = sync_optionhelper_refresh_token("current-secret", project, skill)

    assert result.ok and result.changed
    assert memory.read_text(encoding="utf-8") == "token: current-secret"
    assert "current-secret" not in result.message
    assert "stale-secret" not in result.message


def test_matching_optionhelper_token_is_not_rewritten(tmp_path):
    skill = _fake_skill(tmp_path / "skill")
    project = tmp_path / "project"
    memory = project / ".optionhelper" / "memory.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("token: same-secret", encoding="utf-8")
    before = memory.stat().st_mtime_ns

    result = sync_optionhelper_refresh_token("same-secret", project, skill)

    assert result.ok and not result.changed
    assert memory.stat().st_mtime_ns == before
