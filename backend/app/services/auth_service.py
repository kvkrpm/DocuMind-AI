import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings


_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DUMMY_PASSWORD_HASH = None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=64,
    )
    return "$".join(
        (
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, n, r, p, salt_value, digest_value = password_hash.split("$")
        if algorithm != "scrypt":
            return False
        expected_digest = base64.urlsafe_b64decode(digest_value)
        actual_digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.urlsafe_b64decode(salt_value),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected_digest),
        )
    except (ValueError, TypeError, MemoryError):
        return False
    return hmac.compare_digest(actual_digest, expected_digest)


def _dummy_hash() -> str:
    global _DUMMY_PASSWORD_HASH
    if _DUMMY_PASSWORD_HASH is None:
        _DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(24))
    return _DUMMY_PASSWORD_HASH


def issue_access_token(user_id: str, token_version: int = 0) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user_id,
            "iat": now,
            "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
            "type": "access",
            "ver": token_version,
        },
        settings.AUTH_SECRET_KEY,
        algorithm="HS256",
    )


def decode_access_token(token: str) -> tuple[str, int] | None:
    try:
        payload = jwt.decode(
            token,
            settings.AUTH_SECRET_KEY,
            algorithms=["HS256"],
            options={"require": ["sub", "exp", "iat", "type"]},
        )
    except jwt.InvalidTokenError:
        return None
    if payload.get("type") != "access":
        return None
    subject = payload.get("sub")
    version = payload.get("ver", 0)
    if not isinstance(subject, str) or not isinstance(version, int):
        return None
    return subject, version


def issue_guest_token(guest_id: str, expires_at: datetime) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": guest_id,
            "iat": now,
            "exp": expires_at,
            "type": "guest",
        },
        settings.AUTH_SECRET_KEY,
        algorithm="HS256",
    )


def decode_guest_token(token: str) -> str | None:
    try:
        payload = jwt.decode(
            token,
            settings.AUTH_SECRET_KEY,
            algorithms=["HS256"],
            options={"require": ["sub", "exp", "iat", "type"]},
        )
    except jwt.InvalidTokenError:
        return None
    subject = payload.get("sub")
    if payload.get("type") != "guest" or not isinstance(subject, str):
        return None
    return subject


def create_action_token(expire_minutes: int) -> tuple[str, str, datetime]:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
    return token, token_hash, expires_at


def hash_action_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_login_password(password: str, password_hash: str | None) -> bool:
    return verify_password(password, password_hash or _dummy_hash()) and password_hash is not None
