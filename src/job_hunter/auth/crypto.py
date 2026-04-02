"""Fernet-based encryption for sensitive per-user fields (API keys, passwords).

Uses the application ``SECRET_KEY`` to derive a Fernet key.  Values that are
already plaintext (legacy data written before encryption was added) are
detected by the absence of the Fernet token prefix and returned as-is on
decrypt — this allows seamless migration.
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("job_hunter.auth.crypto")

#: DB columns that store secrets and must be encrypted at rest.
ENCRYPTED_FIELDS: frozenset[str] = frozenset({
    "openai_api_key",
    "resend_api_key",
    "smtp_password",
})

_FERNET_PREFIX = "gAAAAA"


def _derive_fernet_key(secret_key: str) -> bytes:
    """Derive a valid 32-byte url-safe-base64 Fernet key from *secret_key*."""
    raw = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(raw)


def encrypt_value(plaintext: str, secret_key: str) -> str:
    """Encrypt *plaintext* with *secret_key* and return the Fernet token string."""
    if not plaintext or not secret_key:
        return plaintext
    f = Fernet(_derive_fernet_key(secret_key))
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_value(ciphertext: str, secret_key: str) -> str:
    """Decrypt a Fernet token back to plaintext.

    If *ciphertext* is not a valid Fernet token (e.g. legacy plaintext stored
    before encryption was enabled), it is returned unchanged.  This allows
    seamless migration — old plaintext values keep working until they are
    re-saved (at which point they get encrypted).
    """
    if not ciphertext or not secret_key:
        return ciphertext
    # Quick prefix check before attempting full decode
    if not ciphertext.startswith(_FERNET_PREFIX):
        return ciphertext
    try:
        f = Fernet(_derive_fernet_key(secret_key))
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception):
        # Not a valid Fernet token — return as-is (legacy plaintext or
        # encrypted with a different key after rotation).
        return ciphertext


def encrypt_legacy_secrets(engine, secret_key: str) -> int:
    """One-shot migration: encrypt any plaintext secrets in the ``users`` table.

    Reads all user rows, detects plaintext values in :data:`ENCRYPTED_FIELDS`,
    and overwrites them with Fernet-encrypted versions.  Safe to call multiple
    times — already-encrypted values are skipped.

    Returns the number of fields encrypted.
    """
    if not secret_key:
        return 0

    from sqlalchemy import select
    from sqlalchemy.orm import Session, sessionmaker

    # Lazy import to avoid circular dependency at module level
    from job_hunter.auth.models import User

    SessionFactory = sessionmaker(bind=engine)
    session: Session = SessionFactory()
    count = 0
    try:
        users = session.execute(select(User)).scalars().all()
        for user in users:
            for field in ENCRYPTED_FIELDS:
                val = getattr(user, field, None)
                if val and not val.startswith(_FERNET_PREFIX):
                    encrypted = encrypt_value(val, secret_key)
                    setattr(user, field, encrypted)
                    count += 1
        if count:
            session.commit()
            logger.info("crypto: encrypted %d legacy plaintext secret(s)", count)
    except Exception:
        session.rollback()
        logger.debug("crypto: legacy encryption skipped (table may not exist yet)")
    finally:
        session.close()
    return count


