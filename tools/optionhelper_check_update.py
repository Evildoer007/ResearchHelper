"""One-command, read-only OptionHelper update audit for Research Helper.

The command compares the active integration with a checked-out OptionHelper
source tree, builds or accepts a candidate Skill, runs deterministic technical
checks, and writes Markdown + JSON reports.  It never pulls source, installs
dependencies, changes credentials, activates a Skill, or runs a real quote.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# Running this file directly makes ``tools`` the first import root rather than
# the repository root.  Keep project modules importable exactly as they are
# under pytest and the desktop application.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_SOURCE = Path(r"D:\download\OptionHelper")
CRITICAL_PREFIXES = (
    "SKILL.md",
    "core/tool_entry.py",
    "core/requirements.lock",
    "modules/recommender/",
    "modules/pricer/",
    "modules/reporter/",
    "packaging/skill/",
    "packaging/release_contract.py",
)
_SECRET = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret)"
    r"\s*[:=]\s*([^\s,;]+)"
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _clean(text: str, limit: int = 50000) -> str:
    value = _SECRET.sub(r"\1=<redacted>", str(text or "")).replace("\x00", "")
    return value[-limit:]


def _extract_json(text: str) -> dict[str, Any]:
    """Return the outermost JSON object from output that may contain progress noise.

    Looking for the *last* decodable object is incorrect for nested JSON: it
    selects the final child mapping rather than the command's top-level result.
    The outer protocol envelope is the longest successfully decoded object.
    """
    decoder = json.JSONDecoder()
    found: dict[str, Any] = {}
    found_length = -1
    for match in re.finditer(r"\{", str(text or "")):
        try:
            value, end = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and end > found_length:
            found = value
            found_length = end
    return found


@dataclass
class Check:
    key: str
    label: str
    status: str
    detail: str = ""
    duration_seconds: float = 0.0
    command: list[str] = field(default_factory=list)


@dataclass
class Audit:
    generated_at: str
    project_root: str
    source_root: str
    active_skill: str = ""
    active_python: str = ""
    active_commit: str = ""
    candidate_commit: str = ""
    candidate_skill: str = ""
    candidate_python: str = ""
    branch: str = ""
    commits_ahead: int | None = None
    changed_files: list[str] = field(default_factory=list)
    critical_changes: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    technical_status: str = "failed"
    activation_recommendation: str = "不得激活"
    manual_checks: list[str] = field(default_factory=lambda: [
        "用非敏感测试标的完成一次真实小规模报价",
        "核对标的、结构、期限、估值日和合同条款",
        "确认报价表、HTML/PDF 预览与内部底稿来自同一次运行",
        "确认失败、取消和多标的任务不会串用旧结果",
    ])


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _run(command: list[str], *, cwd: Path, timeout: int = 300,
         env: dict[str, str] | None = None) -> tuple[int, str, float]:
    started = time.perf_counter()
    child_env = dict(os.environ)
    if env:
        child_env.update(env)
    # Keep third-party Python command output readable and deterministic on
    # Chinese Windows consoles whose inherited code page may otherwise be GBK.
    child_env.setdefault("PYTHONUTF8", "1")
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=child_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        return result.returncode, _clean(output), round(time.perf_counter() - started, 3)
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or "") + "\n" + (error.stderr or "")
        return 124, _clean(f"运行超时（{timeout}s）\n{output}"), round(time.perf_counter() - started, 3)
    except OSError as error:
        return 127, _clean(f"{type(error).__name__}: {error}"), round(time.perf_counter() - started, 3)


def _add(audit: Audit, key: str, label: str, status: str, detail: str = "",
         duration: float = 0.0, command: list[str] | None = None) -> None:
    detail = _clean(detail).strip()
    audit.checks.append(Check(key, label, status, detail, duration, command or []))
    symbol = {"pass": "✓", "warn": "!", "fail": "✗", "skip": "-"}.get(status, "·")
    print(f"[{symbol}] {label}" + (f"：{detail.splitlines()[0][:160]}" if detail else ""), flush=True)


def _git(source: Path, *args: str, timeout: int = 60) -> tuple[int, str, float]:
    return _run(
        ["git", "-c", f"safe.directory={source.as_posix()}",
         "-c", "core.excludesFile=", "-C", str(source), *args],
        cwd=ROOT,
        timeout=timeout,
    )


def _active_state(project: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    config = _load_json(project / "config.local.json")
    skill = str(config.get("OPTIONHELPER_SKILL_ROOT") or "")
    receipts: list[tuple[float, dict[str, Any]]] = []
    for path in (project / ".optionhelper" / "migrations").glob("*/receipt.json"):
        receipt = _load_json(path)
        if receipt and (not skill or str(receipt.get("skill_root") or "") == skill):
            try:
                modified = path.stat().st_mtime
            except OSError:
                modified = 0
            receipts.append((modified, receipt))
    receipt = max(receipts, default=(0, {}), key=lambda item: item[0])[1]
    return config, receipt


def _git_audit(audit: Audit, source: Path) -> bool:
    if not (source / ".git").exists():
        _add(audit, "git_repo", "OptionHelper 源码仓库", "fail", f"不是 Git 仓库：{source}")
        return False
    code, head, elapsed = _git(source, "rev-parse", "HEAD")
    if code:
        _add(audit, "git_head", "读取候选提交", "fail", head, elapsed)
        return False
    audit.candidate_commit = head.strip().splitlines()[0]
    code, branch, elapsed = _git(source, "branch", "--show-current")
    audit.branch = branch.strip() if code == 0 else ""
    _add(audit, "git_head", "读取候选提交", "pass",
         f"{audit.candidate_commit[:12]}｜分支 {audit.branch or 'detached'}", elapsed)

    code, dirty, elapsed = _git(source, "status", "--porcelain")
    if code:
        _add(audit, "git_clean", "源码工作区状态", "fail", dirty, elapsed)
    elif dirty.strip():
        _add(audit, "git_clean", "源码工作区状态", "fail",
             "工作区存在未提交修改；不能把它当作可复现候选\n" + dirty, elapsed)
    else:
        _add(audit, "git_clean", "源码工作区状态", "pass", "工作区干净", elapsed)

    if not audit.active_commit:
        _add(audit, "git_diff", "新旧提交差异", "warn", "未找到当前激活版本的 source_commit")
        return True
    code, ancestry, elapsed = _git(source, "merge-base", "--is-ancestor", audit.active_commit,
                                   audit.candidate_commit)
    if code:
        _add(audit, "git_ancestry", "提交继承关系", "fail",
             "当前激活提交不是候选提交的祖先；可能发生改写历史或选错仓库", elapsed)
        return True
    _add(audit, "git_ancestry", "提交继承关系", "pass", "候选从当前激活提交向前演进", elapsed)
    code, count, _ = _git(source, "rev-list", "--count",
                          f"{audit.active_commit}..{audit.candidate_commit}")
    if code == 0:
        try:
            audit.commits_ahead = int(count.strip())
        except ValueError:
            audit.commits_ahead = None
    code, names, elapsed = _git(source, "diff", "--name-only",
                                f"{audit.active_commit}..{audit.candidate_commit}", timeout=120)
    if code:
        _add(audit, "git_diff", "新旧提交差异", "fail", names, elapsed)
    else:
        audit.changed_files = [line.strip() for line in names.splitlines() if line.strip()]
        audit.critical_changes = [name for name in audit.changed_files
                                  if any(name == prefix or name.startswith(prefix)
                                         for prefix in CRITICAL_PREFIXES)]
        status = "warn" if audit.changed_files else "pass"
        detail = (f"领先 {audit.commits_ahead if audit.commits_ahead is not None else '未知'} 个提交，"
                  f"变更 {len(audit.changed_files)} 个文件，其中关键接口 {len(audit.critical_changes)} 个")
        _add(audit, "git_diff", "新旧提交差异", status, detail, elapsed)
    return True


def _build_candidate(audit: Audit, source: Path, python: Path, output: Path) -> Path | None:
    builder = source / "packaging" / "skill" / "build_skill.py"
    if not builder.is_file():
        _add(audit, "candidate_build", "构建候选 Skill", "fail", f"缺少构建入口：{builder}")
        return None
    command = [str(python), str(builder), "--output", str(output), "--candidate", "--replace"]
    code, text, elapsed = _run(command, cwd=source, timeout=600)
    skill = output / "option-helper"
    if code or not skill.is_dir():
        detail = text.strip() or f"构建进程退出码 {code}，但没有输出错误信息"
        if code == 0 and not skill.is_dir():
            detail = f"构建进程成功，但未生成预期目录：{skill}\n{detail}"
        _add(audit, "candidate_build", "构建候选 Skill", "fail", detail, elapsed, command)
        return None
    _add(audit, "candidate_build", "构建候选 Skill", "pass", str(skill), elapsed, command)
    return skill


def _candidate_checks(audit: Audit, project: Path, skill: Path, python: Path) -> None:
    audit.candidate_skill = str(skill)
    if not python.is_file():
        _add(audit, "candidate_python", "候选解释器", "fail", f"不存在：{python}")
        return
    audit.candidate_python = str(python)
    try:
        from tools.optionhelper_install import verify_contents
        manifest = verify_contents(skill)
        detail = (f"{manifest.get('release_status', '未知状态')}｜"
                  f"capability {manifest.get('capability_version', '未知')}")
        _add(audit, "skill_integrity", "Skill 文件与哈希完整性", "pass", detail)
    except Exception as error:  # noqa: BLE001
        _add(audit, "skill_integrity", "Skill 文件与哈希完整性", "fail",
             f"{type(error).__name__}: {error}")
        return

    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    env.update(
        OPTIONHELPER_RUNTIME_ROOT=str(project / ".optionhelper" / "runtime"),
        OPTIONHELPER_DATA_ROOT=str(project / "data"),
        OPTIONHELPER_RESULT_ROOT=str(project / "result"),
    )
    readiness = [str(python), str(skill / "scripts" / "environment_check.py"),
                 "--check-readiness", "--skill-root", str(skill), "--project-root", str(project)]
    code, text, elapsed = _run(readiness, cwd=project, timeout=180, env=env)
    ready = False
    if code == 0:
        ready = _extract_json(text).get("ok") is True
    _add(audit, "readiness", "候选环境、依赖、iFind 与 Store", "pass" if ready else "fail",
         "统一就绪检查通过" if ready else text, elapsed, readiness)

    listing = [str(python), str(skill / "scripts" / "tool_entry.py"), "--list"]
    code, text, elapsed = _run(listing, cwd=project, timeout=120, env=env)
    list_ok = False
    if code == 0:
        list_ok = bool(_extract_json(text))
    _add(audit, "tool_catalog", "OptionHelper 工具目录协议", "pass" if list_ok else "fail",
         "tool_entry.py --list 返回结构化目录" if list_ok else text, elapsed, listing)

    smoke = [str(python), str(project / "tools" / "optionhelper_smoke.py"), str(skill)]
    code, text, elapsed = _run(smoke, cwd=project, timeout=300, env=env)
    smoke_ok = False
    if code == 0:
        smoke_ok = _extract_json(text).get("ok") is True
    _add(audit, "integration_smoke", "Research Helper 离线兼容 smoke",
         "pass" if smoke_ok else "fail",
         "双标的推荐、Agent 回执和日期隔离通过" if smoke_ok else text,
         elapsed, smoke)


def _project_tests(audit: Audit, project: Path, python: Path, skip: bool) -> None:
    if skip:
        _add(audit, "project_tests", "Research Helper 全量回归", "skip", "由 --skip-project-tests 跳过")
        return
    pytest_probe = [str(python), "-c", "import pytest"]
    code, _, _ = _run(pytest_probe, cwd=project, timeout=30)
    if code == 0:
        # Only collect the project's tests.  Runtime data/result directories
        # may deliberately have restricted ACLs and are not test modules.
        command = [str(python), "-m", "pytest", "-q", "tests"]
        label = "pytest 全量回归"
    else:
        command = [str(python), "-m", "unittest", "discover", "-s", "tests", "-v"]
        label = "unittest 回归（pytest 不可用，覆盖不完整）"
    code, text, elapsed = _run(command, cwd=project, timeout=900)
    status = "pass" if code == 0 and label.startswith("pytest") else "warn" if code == 0 else "fail"
    detail = (text.splitlines()[-1] if text.splitlines() else label)
    if status == "warn":
        detail = label + "；pytest 风格测试未执行"
    elif status == "fail":
        detail = text.strip() or f"测试进程退出码 {code}，但没有输出错误信息"
    _add(audit, "project_tests", "Research Helper 全量回归", status, detail, elapsed, command)


def _finalize(audit: Audit) -> None:
    statuses = [item.status for item in audit.checks]
    if "fail" in statuses:
        audit.technical_status = "failed"
        audit.activation_recommendation = "技术检查失败，不得激活；先按失败项修复后重跑"
    elif "skip" in statuses:
        audit.technical_status = "incomplete"
        audit.activation_recommendation = "检查不完整，不建议激活；补齐跳过项后重跑"
    else:
        audit.technical_status = "technical_pass_manual_pending"
        audit.activation_recommendation = "技术检查通过；完成一次真实小规模报价人工验收后，方可激活"


def _markdown(audit: Audit) -> str:
    status_label = {
        "failed": "失败",
        "incomplete": "不完整",
        "technical_pass_manual_pending": "技术通过，待人工业务验收",
    }.get(audit.technical_status, audit.technical_status)
    lines = [
        "# OptionHelper 新版接入检查报告",
        "",
        f"- 生成时间：{audit.generated_at}",
        f"- 技术状态：**{status_label}**",
        f"- 激活建议：{audit.activation_recommendation}",
        f"- 当前激活提交：`{audit.active_commit or '未知'}`",
        f"- 候选源码提交：`{audit.candidate_commit or '未知'}`",
        f"- 候选分支：`{audit.branch or '未知'}`",
        f"- 候选 Skill：`{audit.candidate_skill or '未形成'}`",
        "",
        "## 自动检查结果",
        "",
        "| 状态 | 检查项 | 耗时 | 说明 |",
        "|---|---|---:|---|",
    ]
    names = {"pass": "通过", "warn": "提示", "fail": "失败", "skip": "跳过"}
    for item in audit.checks:
        detail = item.detail.replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")
        lines.append(f"| {names.get(item.status, item.status)} | {item.label} | "
                     f"{item.duration_seconds:.1f}s | {detail or '—'} |")
    lines += [
        "",
        "## 版本差异摘要",
        "",
        f"- 候选领先提交数：{audit.commits_ahead if audit.commits_ahead is not None else '未知'}",
        f"- 变更文件数：{len(audit.changed_files)}",
        f"- 关键接口文件数：{len(audit.critical_changes)}",
    ]
    if audit.critical_changes:
        lines += ["", "### 需要重点审阅的文件", ""]
        lines += [f"- `{name}`" for name in audit.critical_changes[:80]]
        if len(audit.critical_changes) > 80:
            lines.append(f"- ……另有 {len(audit.critical_changes) - 80} 个，完整列表见 JSON 报告")
    lines += ["", "## 激活前仍需人工完成", ""]
    lines += [f"- [ ] {item}" for item in audit.manual_checks]
    lines += [
        "",
        "## 安全边界",
        "",
        "本次命令未执行 git pull、依赖安装、凭据修改、Skill 激活或真实报价。",
        "只有人工业务验收完成后，才可单独运行 `tools/optionhelper_install.py --activate`。",
        "",
    ]
    return "\n".join(lines)


def run_audit(*, project: Path, source: Path, candidate_skill: Path | None,
              candidate_python: Path | None, project_python: Path,
              output_dir: Path, skip_project_tests: bool = False) -> tuple[Audit, Path, Path]:
    project, source = project.resolve(), source.resolve()
    # argparse accepts a convenient relative --output-dir, but the upstream
    # builder runs with the OptionHelper repository as cwd.  Resolve it here so
    # it always remains inside the Research Helper workspace.
    output_dir = (project / output_dir).resolve() if not output_dir.is_absolute() else output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    config, receipt = _active_state(project)
    audit = Audit(
        generated_at=_now(), project_root=str(project), source_root=str(source),
        active_skill=str(config.get("OPTIONHELPER_SKILL_ROOT") or receipt.get("skill_root") or ""),
        active_python=str(config.get("OPTIONHELPER_PYTHON") or receipt.get("python") or ""),
        active_commit=str(receipt.get("source_commit") or ""),
    )
    _git_audit(audit, source)
    python = (candidate_python or Path(audit.active_python or sys.executable)).expanduser().resolve()
    skill = candidate_skill.expanduser().resolve() if candidate_skill else None
    if skill is None and audit.candidate_commit:
        skill = _build_candidate(audit, source, python, output_dir / "candidate")
    elif skill is not None:
        _add(audit, "candidate_build", "构建候选 Skill", "pass", f"使用预构建候选：{skill}")
    if skill is not None:
        _candidate_checks(audit, project, skill, python)
    else:
        _add(audit, "candidate_checks", "候选 Skill 技术检查", "fail", "未形成可检查的候选 Skill")
    _project_tests(audit, project, project_python.expanduser().resolve(), skip_project_tests)
    _finalize(audit)
    json_path = output_dir / "optionhelper-update-check.json"
    md_path = output_dir / "optionhelper-update-check.md"
    json_path.write_text(json.dumps(asdict(audit), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(_markdown(audit), encoding="utf-8")
    return audit, md_path, json_path


def main() -> int:
    # Windows 从 VS Code/PowerShell 启动时 stdout 可能仍是 GBK；检查器包含中文和
    # 状态符号，日志编码不能反过来中断真正的技术检查。
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except OSError:
                pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help="已由使用者更新并确认的 OptionHelper 源码仓库")
    parser.add_argument("--candidate-skill", type=Path,
                        help="使用已构建候选；省略时从源码构建 technical candidate")
    parser.add_argument("--candidate-python", type=Path,
                        help="候选独立解释器；省略时读取当前激活 OptionHelper 解释器")
    parser.add_argument("--project-python", type=Path, default=Path(sys.executable),
                        help="运行 Research Helper 回归测试的解释器")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "output" / f"optionhelper-update-check-{_stamp()}")
    parser.add_argument("--skip-project-tests", action="store_true",
                        help="仅用于快速诊断；跳过后报告标为不完整")
    args = parser.parse_args()
    audit, markdown, payload = run_audit(
        project=ROOT,
        source=args.source,
        candidate_skill=args.candidate_skill,
        candidate_python=args.candidate_python,
        project_python=args.project_python,
        output_dir=args.output_dir,
        skip_project_tests=args.skip_project_tests,
    )
    print(f"\n总报告：{markdown}")
    print(f"结构化结果：{payload}")
    print(f"结论：{audit.activation_recommendation}")
    return 0 if audit.technical_status == "technical_pass_manual_pending" else 1


if __name__ == "__main__":
    raise SystemExit(main())
