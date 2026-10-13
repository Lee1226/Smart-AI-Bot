"""JWT creation and verification."""

import os
import secrets
import time
from pathlib import Path
from typing import Optional

import jwt

def _load_jwt_secret() -> str:
    configured = os.getenv("SAB_JWT_SECRET", "").strip()
    if configured:
        if len(configured) < 32:
            raise RuntimeError("SAB_JWT_SECRET must be at least 32 characters")
        return configured

    # Preserve sessions across restarts without shipping a known default key.
    default_data_dir = Path(__file__).resolve().parent.parent / "data"
    path = Path(os.getenv("SAB_DATA_DIR", str(default_data_dir))) / ".jwt_secret"
    try:
        value = path.read_text(encoding="utf-8").strip()
        if len(value) >= 32:
            return value
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
    except FileExistsError:
        value = path.read_text(encoding="utf-8").strip()
    if len(value) < 32:
        raise RuntimeError("Persisted JWT secret is invalid")
    return value


JWT_SECRET = _load_jwt_secret()
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("SAB_JWT_EXPIRE_MINUTES", "480"))  # 8 h



def create_access_token(
    payload: dict,
    expire_minutes: Optional[int] = None,
) -> str:
    """Create a signed JWT with an ``exp`` claim."""
    exp_min = expire_minutes if expire_minutes is not None else JWT_EXPIRE_MINUTES
    now = int(time.time())
    claims = {
        **payload,
        "iat": now,
        "exp": now + exp_min * 60,
    }
    return jwt.encode(claims, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """Verify + decode a JWT. Returns the payload dict or None on any failure."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, Exception):
        return None
