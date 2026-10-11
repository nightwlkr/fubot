import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from backend.config import settings


def _get_key() -> bytes:
    """Get the 32-byte AES-GCM key from settings."""
    key_str = settings.FUBOT_ENCRYPTION_KEY
    if len(key_str) >= 32:
        return key_str[:32].encode("utf-8")
    # Pad or hash to get exactly 32 bytes
    import hashlib
    return hashlib.sha256(key_str.encode("utf-8")).digest()


def encrypt(plaintext: str) -> str:
    """
    Encrypt plaintext using AES-256-GCM.
    Returns base64-encoded ciphertext (includes 12-byte nonce prepended).
    """
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """
    Decrypt base64-encoded ciphertext produced by encrypt().
    Extracts nonce from first 12 bytes, then decrypts.
    """
    key = _get_key()
    raw = base64.b64decode(ciphertext)
    nonce = raw[:12]
    ciphertext_bytes = raw[12:]
    aesgcm = AESGCM(key)
    plaintext_bytes = aesgcm.decrypt(nonce, ciphertext_bytes, None)
    return plaintext_bytes.decode("utf-8")


def mask_key(key: str) -> str:
    """Return last 4 chars of key for logging (never log full key)."""
    if len(key) >= 4:
        return f"****{key[-4:]}"
    return "****"
