"""User accounts, sessions and the audit log (for use in a clinic / hospital network).

Accounts are optional. When config/users.json holds at least one user, every API call needs a login session
(the shared PIN is then not used). Passwords are stored only as PBKDF2-SHA256 hashes (310,000 iterations,
random salt). Manage users with:  python scripts/users.py add|passwd|remove|list

Audit log: logs/audit/<YYYY-MM>.jsonl - one line per event (login, analysis, report, FHIR call) with time,
user, client address, the drug names entered and the verdict. No patient-identifying data is recorded
(the program never asks for names or IDs; age / lab values are not written to the audit log either).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path

from omnipharm.config import PROJECT_ROOT, settings

USERS_FILE = PROJECT_ROOT / "config" / "users.json"
ROLES = ("admin", "clinician")
SESSION_HOURS = 12
ITERATIONS = 310_000
_lock = threading.Lock()
_sessions: dict[str, dict] = {}  # token -> {"user", "role", "expires"}
_fails: dict[str, list[float]] = {}  # client address -> recent failed login times


def _load() -> dict:
    if not USERS_FILE.exists():
        return {}
    return json.loads(USERS_FILE.read_text(encoding="utf-8")).get("users", {})


def _save(users: dict) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = USERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"users": users}, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(USERS_FILE)


def accounts_enabled() -> bool:
    return bool(_load())


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS).hex()


def set_user(name: str, password: str, role: str = "clinician") -> None:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if len(password) < 8:
        raise ValueError("password must have at least 8 characters")
    with _lock:
        users = _load()
        salt = secrets.token_bytes(16)
        users[name] = {"salt": salt.hex(), "hash": _hash(password, salt), "role": role,
                       "updated": time.strftime("%Y-%m-%d %H:%M")}
        _save(users)
        for t in [t for t, s in _sessions.items() if s["user"] == name]:  # password change ends old sessions
            _sessions.pop(t, None)


def remove_user(name: str) -> bool:
    with _lock:
        users = _load()
        if users.pop(name, None) is None:
            return False
        _save(users)
        for t in [t for t, s in _sessions.items() if s["user"] == name]:
            _sessions.pop(t, None)
        return True


def list_users() -> list[dict]:
    return [{"user": n, "role": u["role"], "updated": u.get("updated")} for n, u in sorted(_load().items())]


def login(name: str, password: str, client: str) -> dict | None:
    """-> {"token", "user", "role", "expires"} or None. At most 5 failures per client address per 5 minutes."""
    now = time.time()
    with _lock:
        recent = [t for t in _fails.get(client, []) if now - t < 300]
        _fails[client] = recent
        if len(recent) >= 5:
            raise PermissionError("로그인 실패가 많아 5분간 잠겼습니다.")
    u = _load().get(name)
    ok = bool(u) and hmac.compare_digest(_hash(password, bytes.fromhex(u["salt"])), u["hash"])
    with _lock:
        if not ok:
            _fails.setdefault(client, []).append(now)
            return None
        token = secrets.token_urlsafe(32)
        _sessions[token] = {"user": name, "role": u["role"], "expires": now + SESSION_HOURS * 3600}
        return {"token": token, "user": name, "role": u["role"], "expires": _sessions[token]["expires"]}


def session(token: str | None) -> dict | None:
    if not token:
        return None
    with _lock:
        s = _sessions.get(token)
        if s and s["expires"] > time.time():
            return s
        _sessions.pop(token, None)
        return None


def logout(token: str | None) -> None:
    with _lock:
        _sessions.pop(token or "", None)


# ----------------------------------------------------------------------------------------------- audit log
def audit_dir() -> Path:
    return settings.logs_dir / "audit"


def audit(event: str, user: str | None, client: str, **fields) -> None:
    rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, "user": user or "-", "client": client,
           **fields}
    d = audit_dir()
    d.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False)
    with _lock, open(d / f"{time.strftime('%Y-%m')}.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_audit(month: str, limit: int = 2000) -> list[dict]:
    f = audit_dir() / f"{month}.jsonl"
    if not f.exists():
        return []
    lines = f.read_text(encoding="utf-8").splitlines()[-limit:]
    return [json.loads(x) for x in reversed(lines)]
