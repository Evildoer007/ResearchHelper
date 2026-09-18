"""Validate and activate a separately built OptionHelper Skill, with local rollback.

Run with the explicitly selected OptionHelper Python. Never pulls source, installs
dependencies, changes credentials, or overwrites a previous installation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def ensure_skill_read_access(skill: Path, project: Path, *, platform_name: str | None = None) -> str:
    """Make a Windows Skill readable by the desktop user before activation.

    Candidate packages can be built by a sandbox/service identity and moved into
    the project with a protected DACL.  In that case the files exist, but the
    interactive Research Helper process sees ``import tool_entry`` as a missing
    module.  Restore inheritance and grant read/execute only to the owner of the
    Research Helper workspace; never broaden permissions to Everyone.

    Returns the principal that received access, or an empty string off Windows.
    """
    if (platform_name or sys.platform) != "win32":
        return ""
    skill, project = skill.resolve(), project.resolve()
    owner_env = dict(os.environ, RESEARCH_HELPER_ACL_PROJECT=str(project))
    owner_check = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-Acl -LiteralPath $env:RESEARCH_HELPER_ACL_PROJECT).Owner"],
        cwd=project, env=owner_env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    owner = str(owner_check.stdout or "").strip()
    if owner_check.returncode or not owner:
        raise ValueError("无法确认 Research Helper 工作区所有者，未激活新版 OptionHelper")
    commands = (
        ["icacls", str(skill), "/inheritance:e", "/T", "/C", "/Q"],
        ["icacls", str(skill), "/grant", f"{owner}:(OI)(CI)RX", "/T", "/C", "/Q"],
    )
    for command in commands:
        result = subprocess.run(command, cwd=project, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=120)
        if result.returncode:
            raise ValueError(
                "新版 OptionHelper Skill 的 Windows 读取权限修复失败，未切换配置")
    return owner


def verify_contents(skill: Path) -> dict:
    manifest = json.loads((skill / "capability-manifest.json").read_text(encoding="utf-8"))
    hashes = manifest.get("content_hashes")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("Skill 缺少文件哈希清单")
    for relative, expected in hashes.items():
        target = (skill / relative).resolve()
        if not target.is_relative_to(skill):
            raise ValueError("Skill 哈希清单路径越界")
        if not target.is_file() or sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Skill 文件缺失或被修改：{relative}")
    for relative in ("SKILL.md", "scripts/tool_entry.py", "scripts/environment_check.py"):
        if not (skill / relative).is_file():
            raise ValueError(f"Skill 缺少入口：{relative}")
    return manifest


def activate(skill: Path, source_commit: str, *, project: Path = ROOT) -> Path:
    """Only change local paths after the selected interpreter passes readiness."""
    skill, project = skill.resolve(), project.resolve()
    for key in ("OPTIONHELPER_SKILL_ROOT", "OPTIONHELPER_PYTHON"):
        if os.environ.get(key):
            raise ValueError(f"请先移除覆盖本地配置的环境变量 {key}，再切换")
    manifest = verify_contents(skill)
    # Only adjust ACLs after the signed content tree has been verified.  This
    # prevents an arbitrary unverified path from receiving access changes.
    acl_principal = ensure_skill_read_access(skill, project)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    env.update(OPTIONHELPER_RUNTIME_ROOT=str(project / ".optionhelper" / "runtime"),
               OPTIONHELPER_DATA_ROOT=str(project / "data"),
               OPTIONHELPER_RESULT_ROOT=str(project / "result"),
               RESEARCH_HELPER_OPTIONHELPER_SKILL=str(skill))
    for relative, args in (
        ("scripts/environment_check.py", ["--check-readiness", "--skill-root", str(skill),
                                          "--project-root", str(project)]),
        ("scripts/tool_entry.py", ["--list"]),
        ("__module_import__", []),
    ):
        command = (
            [sys.executable, "-c",
             "import os,sys; from pathlib import Path; "
             "p=Path(os.environ['RESEARCH_HELPER_OPTIONHELPER_SKILL'])/'scripts'; "
             "sys.path.insert(0,str(p)); import tool_entry; print(tool_entry.__file__)"]
            if relative == "__module_import__"
            else [sys.executable, str(skill / relative), *args]
        )
        check = subprocess.run(command, cwd=project, env=env, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120)
        if check.returncode:
            # Do not echo full readiness output: local paths/config may be private.
            label = "tool_entry 模块导入" if relative == "__module_import__" else relative
            raise ValueError(f"新 Skill 未通过 {label} 检查，未切换配置")
        if relative == "__module_import__":
            continue
        result = json.loads(check.stdout)
        if relative.endswith("environment_check.py") and result.get("ok") is not True:
            raise ValueError("新 Skill 未通过统一就绪检查，未切换配置")
    config_path = project / "config.local.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    old_root = config.get("OPTIONHELPER_SKILL_ROOT")
    if not old_root:
        old_root = str(Path.home() / "Desktop" / "option-helper_3" / "option-helper")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = project / ".optionhelper" / "migrations" / stamp
    backup.mkdir(parents=True, exist_ok=False)
    if config_path.exists():
        shutil.copy2(config_path, backup / "config.local.json")
    old = Path(old_root)
    if old.is_dir():
        shutil.copytree(old, backup / "previous-skill", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    interpreter = project / ".optionhelper" / "interpreter.txt"
    if interpreter.exists():
        shutil.copy2(interpreter, backup / "interpreter.txt")
    receipt = {
        "source_commit": source_commit, "skill_root": str(skill),
        "python": sys.executable, "previous_skill_root": old_root,
        "config_existed": config_path.exists(),
        "content_tree_hash": manifest.get("content_tree_hash"),
        "release_status": manifest.get("release_status"),
        "capability_version": manifest.get("capability_version"),
        "windows_acl_principal": acl_principal,
        "activated_at": stamp,
    }
    (backup / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    config.update(OPTIONHELPER_SKILL_ROOT=str(skill), OPTIONHELPER_PYTHON=sys.executable)
    temporary = backup / "config.new.json"
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, config_path)
    return backup


def rollback(backup: Path, *, project: Path = ROOT) -> None:
    """Restore only integration paths; keep later API and other setting changes."""
    backup, project = backup.resolve(), project.resolve()
    if not backup.is_relative_to(project / ".optionhelper" / "migrations"):
        raise ValueError("回退记录必须位于本项目 migrations 目录")
    receipt = json.loads((backup / "receipt.json").read_text(encoding="utf-8"))
    config_path = project / "config.local.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("OPTIONHELPER_SKILL_ROOT") != receipt["skill_root"]:
        raise ValueError("当前安装不属于此迁移记录，未回退其他版本")
    old_config_path = backup / "config.local.json"
    old_config = json.loads(old_config_path.read_text(encoding="utf-8")) if old_config_path.exists() else {}
    config["OPTIONHELPER_SKILL_ROOT"] = str(backup / "previous-skill")
    old_python = old_config.get("OPTIONHELPER_PYTHON")
    if not old_python and (backup / "interpreter.txt").is_file():
        old_python = (backup / "interpreter.txt").read_text(encoding="utf-8").strip()
    if not old_python or not Path(old_python).is_file():
        raise ValueError("旧解释器不可用，未回退")
    verify_contents(Path(config["OPTIONHELPER_SKILL_ROOT"]))
    config["OPTIONHELPER_PYTHON"] = old_python
    temporary = backup / "config.rollback.json"
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, config_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--activate", type=Path)
    mode.add_argument("--rollback", type=Path)
    parser.add_argument("--source-commit", default="")
    args = parser.parse_args()
    if args.activate:
        if not args.source_commit:
            parser.error("--activate requires --source-commit")
        print(activate(args.activate, args.source_commit))
    else:
        rollback(args.rollback)
        print("OptionHelper integration paths restored; restart Research Helper")
