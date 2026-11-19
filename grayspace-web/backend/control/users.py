"""Local accounts and their sessions, kept by the control plane (docs/deployments.md, Sign-in).

    <root>/users.json      the accounts: role, source, password hash (0600)
    <root>/sessions.json   signed-in sessions, by the SHA-256 of their token (0600)

Passwords are hashed with scrypt (hashlib, memory-hard), salted per account. A session token is
random and only its hash is stored, so the file can't be replayed. Workers never see users: the
proxy calls them with the service token and the user's name (control/proxy.py).

    python -m control.users create <username> [--role admin|builder|user]   (task user:create)
"""
import argparse
import base64
import hashlib
import hmac
import os
import re
import secrets
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from control.settings import settings
from control.store import Store

ROLES = ("admin", "builder", "user")
USERNAME_PATTERN = r"^[a-z][a-z0-9._-]{0,31}$"
MIN_PASSWORD = 12
# scrypt: 32 MiB per hash
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
# Write a session's last use at most this often (seconds)
TOUCH_EVERY = 60


class UserError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: Optional[str]) -> bool:
    try:
        scheme, n, r, p, salt, digest = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                                maxmem=64 * 1024 * 1024, dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# A password nobody chose, to compare against when the account doesn't exist: the answer takes as long
_DUMMY_HASH: Optional[str] = None


def dummy_verify(password: str) -> None:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_hex(16))
    verify_password(password, _DUMMY_HASH)


def one_time_password() -> str:
    """Easy to read out: four groups of four from an alphabet without look-alikes."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))


def check_password_rules(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise UserError(422, f"Passwords need at least {MIN_PASSWORD} characters")


_users = Store("users.json")
_sessions = Store("sessions.json")


# ----- Accounts -----

def public(record: Dict) -> Dict:
    return {k: v for k, v in record.items() if k != "password"}


def get(username: str) -> Optional[Dict]:
    return _users.read().get(username)


def all_users() -> List[Dict]:
    return sorted(_users.read().values(), key=lambda u: u["username"])


def any_users() -> bool:
    return bool(_users.read())


def _validate(username: str, role: str) -> None:
    if not re.match(USERNAME_PATTERN, username or ""):
        raise UserError(422, "Usernames are 1-32 lowercase letters, digits, '.', '_' or '-', starting with a letter")
    if role not in ROLES:
        raise UserError(422, f"Role must be one of {', '.join(ROLES)}")


def create(username: str, role: str = "user", name: str = "", email: str = "", password: Optional[str] = None,
           source: str = "local") -> Tuple[Dict, Optional[str]]:
    """A new account. Without a password it gets a one-time one (returned) that must be changed at first sign-in."""
    _validate(username, role)
    one_time = None
    if password is None:
        password = one_time = one_time_password()
    else:
        check_password_rules(password)
    with _users.locked():
        data = dict(_users.read())
        if username in data:
            raise UserError(409, f"A user '{username}' already exists")
        data[username] = {
            "username": username, "name": name or username, "email": email, "role": role, "source": source, "disabled": False,
            "must_change_password": one_time is not None, "created": now(), "last_seen": None, "password": hash_password(password),
        }
        _users.write(data)
        return data[username], one_time


def update(username: str, **fields) -> Dict:
    with _users.locked():
        data = dict(_users.read())
        record = data.get(username)
        if record is None:
            raise UserError(404, f"No user '{username}'")
        if "role" in fields:
            _validate(username, fields["role"])
        record = {**record, **fields}
        data[username] = record
        _users.write(data)
    if fields.get("disabled") or "role" in fields:
        end_sessions(username)
    return record


def delete(username: str) -> None:
    with _users.locked():
        data = dict(_users.read())
        if data.pop(username, None) is None:
            raise UserError(404, f"No user '{username}'")
        _users.write(data)
    end_sessions(username)


def set_password(username: str, password: str, must_change: bool = False) -> Dict:
    if not must_change:
        check_password_rules(password)
    return _set_hash(username, hash_password(password), must_change)


def _set_hash(username: str, password_hash: str, must_change: bool) -> Dict:
    with _users.locked():
        data = dict(_users.read())
        if username not in data:
            raise UserError(404, f"No user '{username}'")
        data[username] = {**data[username], "password": password_hash, "must_change_password": must_change, "password_changed": now()}
        _users.write(data)
        return data[username]


def reset_password(username: str) -> str:
    """A one-time password for the account; its sessions end."""
    password = one_time_password()
    _set_hash(username, hash_password(password), True)
    end_sessions(username)
    return password


def authenticate(username: str, password: str) -> Optional[Dict]:
    """The account when the password is right and it may sign in; None otherwise (as slowly either way)."""
    record = get(username)
    if record is None or record.get("source") != "local":
        dummy_verify(password)
        return None
    if not verify_password(password, record.get("password")):
        return None
    return record


def ensure(username: str, role: str, password: Optional[str]) -> Optional[str]:
    """Create the account unless it exists. The one-time password when one was made."""
    if get(username) is not None:
        return None
    try:
        _, one_time = create(username, role, password=password)
    except UserError as e:
        if e.status == 409:  # another process made it first
            return None
        raise
    return one_time


# ----- Sessions -----

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(username: str, ip: str = "", agent: str = "") -> Tuple[str, Dict]:
    token = "gss_" + secrets.token_urlsafe(32)
    t = time.time()
    session = {"id": secrets.token_hex(8), "user": username, "created": now(), "last_used": t, "expires": t + settings.session_ttl,
               "ip": ip or "", "agent": (agent or "")[:200]}
    with _sessions.locked():
        data = {k: v for k, v in _sessions.read().items() if v["expires"] > t}
        data[_token_hash(token)] = session
        _sessions.write(data)
    with _users.locked():
        users = dict(_users.read())
        if username in users:
            users[username] = {**users[username], "last_seen": now()}
            _users.write(users)
    return token, session


def resolve_session(token: str) -> Optional[Dict]:
    """The live session for the token, its expiry slid forward; None when there is none."""
    if not token.startswith("gss_"):
        return None
    key = _token_hash(token)
    session = _sessions.read().get(key)
    t = time.time()
    if session is None or session["expires"] <= t:
        return None
    if t - session["last_used"] > TOUCH_EVERY:
        with _sessions.locked():
            data = dict(_sessions.read())
            if key in data:
                data[key] = session = {**data[key], "last_used": t, "expires": t + settings.session_ttl}
                _sessions.write(data)
    return session


def end_session(session_id: str, username: Optional[str] = None) -> bool:
    with _sessions.locked():
        data = dict(_sessions.read())
        keys = [k for k, v in data.items() if v["id"] == session_id and (username is None or v["user"] == username)]
        for k in keys:
            del data[k]
        if keys:
            _sessions.write(data)
        return bool(keys)


def end_sessions(username: str, keep: Optional[str] = None) -> None:
    """End every session of the user's, except the one with ID keep."""
    with _sessions.locked():
        data = _sessions.read()
        kept = {k: v for k, v in data.items() if v["user"] != username or v["id"] == keep}
        if len(kept) != len(data):
            _sessions.write(kept)


def user_sessions(username: str) -> List[Dict]:
    t = time.time()
    return sorted((v for v in _sessions.read().values() if v["user"] == username and v["expires"] > t), key=lambda s: s["created"])


# ----- Startup and the command line -----

def prepare(log=print) -> None:
    """Create the bootstrap admin (with a one-time password, logged) and the development seed users."""
    if settings.bootstrap_admin:
        one_time = ensure(settings.bootstrap_admin, "admin", None)
        if one_time:
            log(f"Created the bootstrap admin '{settings.bootstrap_admin}'. One-time password (change it at first sign-in): {one_time}")
    if not any_users() and not settings.seed_user_list:
        log("No accounts yet: set GRAYSPACE_BOOTSTRAP_ADMIN, or run task user:create NAME=<name> ROLE=admin. "
            "The service token (GRAYSPACE_API_TOKEN) still works")
    if settings.seed_user_list:
        if not settings.seed_password:
            log("GRAYSPACE_SEED_USERS is set without GRAYSPACE_SEED_PASSWORD; no seed users created")
            return
        for username, role in settings.seed_user_list:
            ensure(username, role, settings.seed_password)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m control.users", description="Manage the control plane's local accounts")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create", help="Create a local account; prints its one-time password")
    c.add_argument("username")
    c.add_argument("--role", default="user", choices=ROLES)
    c.add_argument("--name", default="")
    c.add_argument("--email", default="")
    r = sub.add_parser("reset-password", help="Give an account a new one-time password")
    r.add_argument("username")
    sub.add_parser("list", help="List the accounts")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            _, one_time = create(args.username, args.role, args.name, args.email)
            print(f"Created {args.role} '{args.username}'. One-time password (change it at first sign-in): {one_time}")
        elif args.command == "reset-password":
            print(f"One-time password for '{args.username}': {reset_password(args.username)}")
        else:
            for u in all_users():
                print(f"{u['username']:<20} {u['role']:<8} {u['source']:<6} {'disabled' if u.get('disabled') else ''}")
    except UserError as e:
        print(e.detail, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
