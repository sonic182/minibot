from __future__ import annotations

import base64
import hashlib
import os
from typing import Any

_VERSION = 1
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_BYTES = 32
_SALT_BYTES = 16
_NONCE_BYTES = 12


def derive_key(password: str, salt: bytes, *, n: int = _SCRYPT_N, r: int = _SCRYPT_R, p: int = _SCRYPT_P) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_KEY_BYTES, maxmem=n * r * 256)


def encrypt(plaintext: bytes, password: str) -> dict[str, Any]:
    """Encrypt ``plaintext`` into a self-describing envelope (AES-256-GCM, scrypt-derived key)."""
    aesgcm = _aesgcm_class()
    salt = os.urandom(_SALT_BYTES)
    nonce = os.urandom(_NONCE_BYTES)
    key = derive_key(password, salt)
    ciphertext = aesgcm(key).encrypt(nonce, plaintext, None)
    return {
        "version": _VERSION,
        "kdf": "scrypt",
        "n": _SCRYPT_N,
        "r": _SCRYPT_R,
        "p": _SCRYPT_P,
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }


def decrypt(envelope: dict[str, Any], password: str) -> bytes:
    version = envelope.get("version")
    if version != _VERSION:
        raise ValueError(f"unsupported vault version: {version!r}")
    if envelope.get("kdf") != "scrypt":
        raise ValueError(f"unsupported vault kdf: {envelope.get('kdf')!r}")
    aesgcm = _aesgcm_class()
    try:
        salt = base64.b64decode(envelope["salt"])
        nonce = base64.b64decode(envelope["nonce"])
        ciphertext = base64.b64decode(envelope["ciphertext"])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"malformed vault envelope: {exc}") from exc
    key = derive_key(password, salt, n=int(envelope["n"]), r=int(envelope["r"]), p=int(envelope["p"]))
    try:
        return aesgcm(key).decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise ValueError("vault password is incorrect or the vault file is corrupt") from exc


def _aesgcm_class() -> Any:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise ValueError(
            "the credential vault needs the `cryptography` package: poetry install --extras vault"
        ) from exc
    return AESGCM
