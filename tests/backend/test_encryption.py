"""Encryption tests — AES-256-GCM roundtrip, tamper detection, key masking."""
import pytest

from backend.encryption import encrypt, decrypt, mask_key


def test_roundtrip():
    ct = encrypt("my-secret-api-key")
    assert ct != "my-secret-api-key"
    assert decrypt(ct) == "my-secret-api-key"


def test_ciphertexts_are_unique_per_call():
    """Fresh nonce each time — same plaintext must never map to the same ciphertext."""
    assert encrypt("same") != encrypt("same")


def test_plaintext_not_embedded():
    ct = encrypt("PROBESECRET")
    assert "PROBE" not in ct


def test_tamper_detected():
    import base64
    ct = encrypt("data")
    raw = bytearray(base64.b64decode(ct))
    raw[-1] ^= 0xFF
    with pytest.raises(Exception):
        decrypt(base64.b64encode(bytes(raw)).decode())


def test_mask_key():
    assert mask_key("PROBEKEY123") == "****Y123"
    assert mask_key("ab") == "****"
