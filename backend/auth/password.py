"""Password hashing with bcrypt."""

import bcrypt


def hash_password(password: str) -> str:
    """Return a bcrypt hash of *password*."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Check *password* against a bcrypt *hashed* string."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
