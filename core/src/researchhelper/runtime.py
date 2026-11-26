"""Explicit local execution consent, separate from model and source secrets.

Demo and acceptance launchers are locked by default. The native App may allow
an authenticated administrator to enable external research. Merely saving a
credential never changes the network policy or a task's spent budget.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from .contracts import ContractError, LocalStateError, unique_json_object
from .credentials import commit_configuration, configuration_lock, owned_bytes


class StoreLease:
    """Hold one OS-backed lease for the service or a Skill research task.

    Keep the inode after release. Unlinking a lock file would let a new process
    lock a different inode while an existing owner still holds the old one.
    The lease contains no PID, credentials or research state.
    """
    def __init__(self, store, *, task_id=None):
        self.stream = None
        root = Path(store).resolve()
        if task_id is None:
            name = '.researchhelper-service.lock'
            unavailable = '研究数据目录已被其他服务使用或无法锁定，请关闭原服务后重试；未恢复或派发任务'
        else:
            name = '.researchhelper-task-' + hashlib.sha256(task_id.encode()).hexdigest() + '.lock'
            unavailable = '该研究任务正在其他会话执行或暂时无法锁定，请稍后恢复'
        try:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = root / name
            try:
                existing = path.lstat()
                if not stat.S_ISREG(existing.st_mode):
                    raise OSError('Invalid service lease')
            except FileNotFoundError:
                pass
            flags = (os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
                     | getattr(os, 'O_NONBLOCK', 0))
            descriptor = os.open(path, flags, 0o600)
            try:
                self.stream = os.fdopen(descriptor, 'r+b')
            except BaseException:
                os.close(descriptor)
                raise
            metadata = os.fstat(self.stream.fileno())
            if (not stat.S_ISREG(metadata.st_mode)
                    or hasattr(os, 'getuid') and metadata.st_uid != os.getuid()):
                raise OSError('Invalid service lease')
            visible = path.lstat()
            if (not stat.S_ISREG(visible.st_mode)
                    or (metadata.st_dev, metadata.st_ino) != (visible.st_dev, visible.st_ino)):
                raise OSError('Service lease path changed')
            if hasattr(os, 'fchmod'):
                os.fchmod(self.stream.fileno(), 0o600)
            if os.name == 'nt':
                import msvcrt
                # A Windows byte-range lock needs one persistent sentinel.
                if metadata.st_size == 0:
                    self.stream.write(b'\0')
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.close()
            raise LocalStateError('store_unavailable', unavailable) from None

    def close(self):
        stream = self.stream
        if stream is not None:
            # Closing releases the OS lock, including on process termination.
            stream.close()
            self.stream = None


class ExecutionPolicy:
    def __init__(self, store, configurable=False):
        if not isinstance(configurable, bool):
            raise ContractError("运行策略管理权限须由启动器明确指定")
        self.path = Path(store) / "execution-policy.json"
        self.configurable = configurable
        self.lock = configuration_lock(self.path)

    def _read(self):
        try:
            raw = owned_bytes(self.path)
            if raw is None:
                return None, {"enabled": False, "policy_version": 1}
            value = json.loads(raw, object_pairs_hook=unique_json_object)
            if (not isinstance(value, dict) or set(value) != {"enabled", "policy_version"}
                    or not isinstance(value["enabled"], bool)
                    or type(value["policy_version"]) is not int or value["policy_version"] < 1):
                raise ValueError()
            return raw, value
        except (OSError, ValueError, RecursionError):
            raise LocalStateError("execution_policy_unavailable",
                "运行策略无法读取，原件保留；不能自动开启外部调用") from None

    def read(self):
        with self.lock:
            _, value = self._read()
            return value

    def save(self, value, *, expected=None):
        if not self.configurable:
            raise ContractError("此运行环境锁定离线验收，不允许开启真实调用")
        if (not isinstance(value, dict) or set(value) != {"enabled", "confirm"}
                or not isinstance(value["enabled"], bool) or value["confirm"] is not True):
            raise ContractError("切换运行模式须明确选择并确认")
        with self.lock:
            raw, old = self._read()
            if expected is not None and old != expected:
                raise ContractError("运行策略文件已变化，请重新打开App确认；未保存")
            if old["enabled"] == value["enabled"]:
                return old
            saved = {"enabled": value["enabled"], "policy_version": old["policy_version"] + 1}
            commit_configuration(self.path, saved, raw)
            return saved
