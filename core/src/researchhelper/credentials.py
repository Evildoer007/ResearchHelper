"""ResearchHelper-local secrets, adapted from OH's reference/store separation.

Settings contain purpose-bound opaque references. Values are held in owner-only
files outside tasks and reports. No system-vault prompts, environment fallback,
foreign product stores or credentials copied into delivery resources.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import threading

from .contracts import ContractError

_LOCKS = {}
_LOCK_GUARD = threading.Lock()
PURPOSES = frozenset({"model-service", "ifind-access_token", "ifind-refresh_token", "tinyshare-token"})


def protect_private_path(path):
    """OH's owner-only POSIX/Windows permission policy, no shell or prompts."""
    path = Path(path)
    if path.is_symlink():
        raise ContractError("本机凭据不能使用软链接")
    if os.name != "nt":
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    pointer = ctypes.c_void_p
    signatures = (
        (kernel.GetCurrentProcess, [], wintypes.HANDLE),
        (kernel.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
        (kernel.LocalFree, [pointer], pointer),
        (security.OpenProcessToken, [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)], wintypes.BOOL),
        (security.GetTokenInformation, [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        (security.ConvertSidToStringSidW, [pointer, ctypes.POINTER(pointer)], wintypes.BOOL),
        (security.ConvertStringSecurityDescriptorToSecurityDescriptorW, [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(pointer), pointer], wintypes.BOOL),
        (security.SetFileSecurityW, [wintypes.LPCWSTR, wintypes.DWORD, pointer], wintypes.BOOL),
    )
    for function, arguments, result in signatures:
        function.argtypes, function.restype = arguments, result

    def checked(success):
        if not success:
            raise ctypes.WinError(ctypes.get_last_error())

    token, sid_text, descriptor = wintypes.HANDLE(), pointer(), pointer()
    checked(security.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)))
    try:
        size = wintypes.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        user = ctypes.create_string_buffer(size.value)
        checked(security.GetTokenInformation(token, 1, user, size, ctypes.byref(size)))
        sid = ctypes.cast(user, ctypes.POINTER(pointer))[0]
        checked(security.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)))
        inheritance = "OICI" if path.is_dir() else ""
        sddl = f"D:P(A;{inheritance};FA;;;{ctypes.wstring_at(sid_text)})"
        checked(security.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None))
        checked(security.SetFileSecurityW(str(path), 0x80000004, descriptor))
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if sid_text:
            kernel.LocalFree(sid_text)
        kernel.CloseHandle(token)


def configuration_lock(path):
    """All settings instances for one local file share their transaction lock."""
    with _LOCK_GUARD:
        return _LOCKS.setdefault(str(Path(path).absolute()), threading.RLock())


def owned_bytes(path, *, maximum=131072, allow_multiple_links=False):
    path = Path(path)
    if path.is_symlink():
        raise ContractError("本机配置不能使用软链接")
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        if path.is_symlink():
            raise ContractError("本机配置不能使用软链接") from None
        return None
    except OSError:
        raise ContractError("本机配置无法安全读取") from None
    try:
        metadata = os.fstat(descriptor)
        if maximum is None:
            maximum = metadata.st_size
        if (not stat.S_ISREG(metadata.st_mode) or (not allow_multiple_links and metadata.st_nlink != 1) or
                os.name != "nt" and metadata.st_uid != os.getuid() or metadata.st_size > maximum):
            raise ContractError("本机配置文件状态异常")
        raw = os.read(descriptor, maximum + 1)
        if len(raw) > maximum or len(raw) != metadata.st_size:
            raise ContractError("本机配置内容未完整读取")
        return raw
    finally:
        os.close(descriptor)


def private_directory(path):
    path = Path(path)
    if path.is_symlink():
        raise ContractError("本机凭据目录不能使用软链接")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir() or os.name != "nt" and path.stat().st_uid != os.getuid():
        raise ContractError("本机凭据目录状态异常")
    protect_private_path(path)


def commit_configuration(path, payload, expected):
    """Publish settings only after new immutable credential records are verified."""
    path = Path(path)
    private_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.stem + "-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            protect_private_path(temporary)
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        if owned_bytes(path) != expected:
            raise ContractError("本机配置在保存期间已变化，未覆盖")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class LocalSecretStore:
    """Purpose-scoped OH-style local-secret records, owned only by RH."""
    MAX_SECRET_BYTES = 65536

    def __init__(self, store):
        store = Path(store)
        if store.is_symlink():
            raise ContractError("本机数据目录不能使用软链接")
        self.root = store / "credentials"

    @staticmethod
    def validate(reference, purpose):
        if purpose not in PURPOSES:
            raise ContractError("未登记的ResearchHelper凭据用途")
        if (not isinstance(reference, dict) or set(reference) != {"provider", "key"}
                or reference.get("provider") != "local-secret"
                or not isinstance(reference.get("key"), str)
                or not re.fullmatch("researchhelper/" + re.escape(purpose) + r"/[0-9a-f]{32}", reference["key"])):
            raise ContractError("本机凭据引用与ResearchHelper用途不匹配")
        return reference

    def path(self, reference, purpose):
        self.validate(reference, purpose)
        if self.root.is_symlink():
            raise ContractError("本机凭据目录不能使用软链接")
        material = (reference["provider"] + "\0" + reference["key"]).encode()
        return self.root / (hashlib.sha256(material).hexdigest() + ".secret")

    def resolve(self, reference, purpose):
        target = self.path(reference, purpose)
        try:
            raw = owned_bytes(target, maximum=self.MAX_SECRET_BYTES)
            if raw is None or os.name != "nt" and stat.S_IMODE(target.stat().st_mode) != 0o600:
                raise ContractError("未找到有效的本机凭据，请在RH设置中重新保存")
            value = raw.decode("utf-8")
            if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise ContractError("本机凭据内容无效，请在RH设置中重新保存")
            return value
        except (OSError, UnicodeDecodeError):
            raise ContractError("本机凭据无法读取，请在RH设置中重新保存") from None

    def create(self, value, purpose):
        if not isinstance(value, str) or not value.strip() or len(value.encode()) > self.MAX_SECRET_BYTES or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ContractError("本机凭据内容无效")
        reference = {"provider": "local-secret", "key": "researchhelper/" + purpose + "/" + secrets.token_hex(16)}
        target = self.path(reference, purpose)
        private_directory(self.root.parent)
        private_directory(self.root)
        # Immutable records make a two-file update safe: the old reference
        # continues to resolve until settings atomically adopt this new one.
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                protect_private_path(target)
                stream.write(value.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            if not hmac.compare_digest(self.resolve(reference, purpose).encode(), value.encode()):
                raise ContractError("本机凭据回读校验未通过")
            return reference
        except Exception:
            if target.exists() and not target.is_symlink():
                target.unlink()
            raise

    def delete(self, reference, purpose):
        """Delete only an exact RH-owned superseded reference, never sweep a folder."""
        target = self.path(reference, purpose)
        if owned_bytes(target, maximum=self.MAX_SECRET_BYTES) is not None:
            target.unlink()


def replace_references(path, store, public, values, old_references, expected):
    """Commit a secret-free config; rollback only new unadopted records on failure.

values maps purpose to a credential; the returned refs are also passed to the
caller-provided serializer. A blank value means explicit removal. Superseded
records are removed only after commit; cleanup failures never revoke a working
new credential or remove an arbitrary/unreferenced file.
"""
    created, adopted = {}, {}
    committed = False
    try:
        for purpose, value in values.items():
            prior = old_references.get(purpose)
            if not value:
                adopted[purpose] = None
            else:
                try:
                    unchanged = prior and hmac.compare_digest(store.resolve(prior, purpose).encode(), value.encode())
                except ContractError:
                    unchanged = False  # explicitly supplied values may repair a missing record
                if unchanged:
                    adopted[purpose] = prior
                else:
                    adopted[purpose] = created[purpose] = store.create(value, purpose)
        commit_configuration(path, public(adopted), expected)
        committed = True
    finally:
        if not committed:
            for purpose, reference in created.items():
                store.delete(reference, purpose)
    for purpose, prior in old_references.items():
        if prior and prior != adopted.get(purpose):
            try:
                store.delete(prior, purpose)
            except (OSError, ContractError):
                # A post-commit cleanup failure is recoverable; it must not be
                # reported as a failed save that would encourage paid retries.
                pass
    return adopted
