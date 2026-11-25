"""Shared encryption helpers for model-repository credentials."""

import os
from pathlib import Path

from cryptography.fernet import Fernet


def _cipher() -> Fernet:
    """Load the installation-wide Fernet key, creating it safely on first use."""
    configured = os.getenv("SAB_LLM_ENCRYPTION_KEY", "").strip()
    if configured:
        try:
            return Fernet(configured.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise RuntimeError("SAB_LLM_ENCRYPTION_KEY must be a valid Fernet key") from exc

    data_dir = Path(
        os.getenv("SAB_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))
    )
    key_path = data_dir / ".llm_encryption_key"
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        key = key_path.read_bytes()
    except FileNotFoundError:
        generated = Fernet.generate_key()
        try:
            fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(generated)
            key = generated
        except FileExistsError:
            key = key_path.read_bytes()

    try:
        return Fernet(key.strip())
    except ValueError as exc:
        raise RuntimeError(f"Invalid LLM encryption key file: {key_path}") from exc


def encrypt_secret(value: str) -> str:
    """Encrypt a credential; empty values remain empty."""
    return _cipher().encrypt(value.encode("utf-8")).decode("ascii") if value else ""


def decrypt_secret(value: str) -> str:
    """Decrypt a stored credential; empty values remain empty."""
    return _cipher().decrypt(value.encode("ascii")).decode("utf-8") if value else ""
