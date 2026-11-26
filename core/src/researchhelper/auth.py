"""Independent local admin/sales access control, not an OptionHelper account service.

Passwords use salted scrypt. Only hashed remembered sessions are retained.
This guards the loopback UI; it does not claim to encrypt the research archive.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from math import inf
from pathlib import Path

from .contracts import ContractError, LocalStateError, unique_json_object
from .credentials import owned_bytes


class AuthenticationRequired(ContractError):
    pass


class PermissionDenied(ContractError):
    pass


class LocalAccount:
    def __init__(self, store):
        self.root = Path(store)
        self.profile = self.root / "local-account.json"
        self.session_file = self.root / "local-sessions.json"
        self.lock = threading.RLock()
        self.failures = []
        self.sessions = self._read(self.session_file, {}, "sessions_unavailable")
        for key, record in self.sessions.items():
            expires = record.get("expires") if isinstance(record, dict) else None
            if (not isinstance(key, str) or len(key) != 64 or not isinstance(record, dict)
                    or not isinstance(record.get("account"), str)
                    or type(expires) not in (int, float) or not 0 < expires < inf
                    or not isinstance(record.get("remember"), bool)
                    or not isinstance(record.get("generation"), str)):
                raise LocalStateError("sessions_unavailable", "本地会话记录格式无效，请恢复记录")

    @staticmethod
    def _read(path, default, code):
        profile = code == "account_unavailable"
        try:
            # First-time account setup briefly publishes a second hard link
            # to a fully written file. Readers must tolerate that transition.
            raw = owned_bytes(path, maximum=None, allow_multiple_links=profile)
            if raw is None:
                return default
            value = json.loads(raw, object_pairs_hook=unique_json_object)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (OSError, ValueError, RecursionError):
            label = "账户" if profile else "会话"
            raise LocalStateError(code, f"本地{label}记录无法读取，原件保留；请先恢复记录，不能重建覆盖") from None

    def _write(self, path, value, exclusive=False):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, name = tempfile.mkstemp(prefix=".local-auth-", dir=self.root)
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(value, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(name, 0o600)
            if exclusive:
                try:
                    os.link(name, path)  # cannot overwrite an existing owner, including concurrent setup
                except FileExistsError:
                    raise ContractError("本地账户已存在，请登录，不覆盖旧账户") from None
            else:
                os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def _password(password, salt):
        return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32).hex()

    def _owner(self):
        owner = self._read(self.profile, None, "account_unavailable")
        if owner is not None:
            try:
                if owner.get("version") != 2 or not isinstance(owner.get("users"), dict):
                    raise ValueError()
                if owner["users"].get("admin", {}).get("role") != "admin":
                    raise ValueError()
                for name, user in owner["users"].items():
                    if (name not in ("admin", "sales") or user["role"] != name
                            or len(bytes.fromhex(user["salt"])) != 16
                            or len(bytes.fromhex(user["password_hash"])) != 32):
                        raise ValueError()
            except (ValueError, TypeError, KeyError, AttributeError):
                raise LocalStateError("account_unavailable", "本地账户记录格式无效，不能改写原账户") from None
        return owner

    def _distinct_sales_password(self, owner, password):
        admin = owner["users"]["admin"]
        candidate = self._password(password, admin["salt"])
        if hmac.compare_digest(admin["password_hash"], candidate):
            raise ContractError("admin与sales须设置不同密码")

    def status(self, cookie=None):
        with self.lock:
            owner = self._owner()
            session = self.current(cookie)
            return {"configured": owner is not None, "authenticated": session is not None,
                    "account": session["account"] if session else None, "role": session["role"] if session else None,
                    "sales_configured": "sales" in owner["users"] if owner and session and session["role"] == "admin" else None,
                    "local_only": True, "archive_encrypted": False}

    @staticmethod
    def _digest(cookie):
        return hashlib.sha256(cookie.encode()).hexdigest() if isinstance(cookie, str) and len(cookie) <= 200 else ""

    def current(self, cookie):
        with self.lock:
            record = self.sessions.get(self._digest(cookie))
            owner = self._owner()
            if isinstance(record, dict) and record.get("expires", 0) > time.time() and owner:
                user = owner["users"].get(record["account"])
                if user and record.get("generation") == user["salt"]:
                    return dict(record, role=user["role"])
            return None

    def require_admin(self, cookie):
        if self.require(cookie)["role"] != "admin":
            raise PermissionDenied("当前账户无管理权限")

    def create_sales(self, cookie, name, password):
        with self.lock:
            self.require_admin(cookie)
            if name != "sales" or not isinstance(password, str) or not 8 <= len(password) <= 4096:
                raise ContractError("请为sales设置8至4096位独立密码")
            owner = self._owner()
            if "sales" in owner["users"]:
                raise ContractError("sales账户已创建，不能覆盖")
            self._distinct_sales_password(owner, password)
            salt = secrets.token_hex(16)
            owner["users"]["sales"] = {"role": "sales", "salt": salt, "password_hash": self._password(password, salt)}
            self._write(self.profile, owner)
            return {"account": "sales", "role": "sales", "created": True}

    def setup(self, admin_password, sales_password):
        with self.lock:
            if self._owner() is not None:
                raise ContractError("本地账户已初始化，请登录")
            if any(not isinstance(value, str) or not 8 <= len(value) <= 4096 for value in (admin_password, sales_password)):
                raise ContractError("两个账户均须设置8至4096位密码")
            if admin_password == sales_password:
                raise ContractError("admin与sales须设置不同密码")
            users = {}
            for name, password in (("admin", admin_password), ("sales", sales_password)):
                salt = secrets.token_hex(16)
                users[name] = {"role": name, "salt": salt, "password_hash": self._password(password, salt)}
            self._write(self.profile, {"version": 2, "users": users}, exclusive=True)
            return {"configured": True}

    def ensure_default_accounts(self):
        """Initialize a fresh local installation without an account wizard.

        Existing accounts and password generations are never replaced. Presets
        match the product reference, but verifiers and sessions stay independent.
        The exclusive initial write also protects concurrent fresh launches.
        """
        with self.lock:
            if self._owner() is not None:
                return
            try:
                self.setup("66666666", "88888888")
            except ContractError:
                # A competing launcher may have won the exclusive first write.
                # Corruption or a genuine save failure must still fail closed.
                if self._owner() is None:
                    raise

    def reset_sales(self, cookie, password):
        with self.lock:
            self.require_admin(cookie)
            if not isinstance(password, str) or not 8 <= len(password) <= 4096:
                raise ContractError("sales密码须为8至4096位")
            owner = self._owner()
            self._distinct_sales_password(owner, password)
            salt = secrets.token_hex(16)
            owner["users"]["sales"] = {"role": "sales", "salt": salt, "password_hash": self._password(password, salt)}
            self._write(self.profile, owner)
            self.sessions = {key: value for key, value in self.sessions.items() if value["account"] != "sales"}
            self._save_sessions()
            return {"account": "sales", "reset": True}

    def require(self, cookie):
        value = self.current(cookie)
        if value is None:
            raise AuthenticationRequired("请先登录ResearchHelper本地账户")
        return value

    def _save_sessions(self):
        current = time.time()
        self.sessions = {key: value for key, value in self.sessions.items() if value.get("expires", 0) > current}
        self._write(self.session_file, {key: value for key, value in self.sessions.items() if value.get("remember")})

    def login(self, account, password, remember=False):
        if not isinstance(account, str) or not account.strip() or len(account.strip()) > 128:
            raise ContractError("请输入本地账户名称，最长128字")
        if not isinstance(password, str) or not password or len(password) > 4096 or not isinstance(remember, bool):
            raise ContractError("请输入有效密码，保持登录须为明确选项")
        account = account.strip()
        with self.lock:
            current = time.time()
            self.failures = [at for at in self.failures if current - at < 90]
            if len(self.failures) >= 5:
                raise ContractError("尝试次数过多，请90秒后重试")
            owner = self._owner()
            if owner is None:
                raise AuthenticationRequired("请先初始化本地账户")
            else:
                user = owner["users"].get(account)
                try:
                    candidate = self._password(password, user["salt"] if user else "0" * 32)
                except ValueError:
                    raise ContractError("本地账户记录格式无效，请恢复记录") from None
                if not user or not hmac.compare_digest(user["password_hash"], candidate):
                    self.failures.append(current)
                    raise AuthenticationRequired("本地账户或密码不正确")
            cookie = secrets.token_urlsafe(32)
            lifetime = 30 * 86400 if remember else 12 * 3600
            self.sessions[self._digest(cookie)] = {"account": account, "expires": current + lifetime,
                                                  "remember": remember, "generation": owner["users"][account]["salt"]}
            self._save_sessions()
            self.failures.clear()
            return cookie, {"account": account, "role": owner["users"][account]["role"], "local_only": True, "remember": remember}

    def logout(self, cookie):
        with self.lock:
            self.sessions.pop(self._digest(cookie), None)
            self._save_sessions()
