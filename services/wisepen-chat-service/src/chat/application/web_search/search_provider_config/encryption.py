from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_CIPHERTEXT_VERSION = "v1"
_NONCE_SIZE_BYTES = 12
_AES_256_KEY_SIZE_BYTES = 32
MASTER_KEY_REQUIRED_MESSAGE = (
    "SEARCH_PROVIDER_CREDENTIAL_MASTER_KEY is required when using stored custom "
    "search provider credentials."
)


class CredentialEncryptionError(Exception):
    pass


class CredentialDecryptionError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class EncryptedCredential:
    encrypted_value: str
    encryption_key_id: str
    key_fingerprint: str
    key_prefix4: str
    key_last4: str


def decode_master_key(raw_value: Optional[str]) -> bytes:
    value = (raw_value or "").strip()
    if not value:
        raise CredentialEncryptionError(MASTER_KEY_REQUIRED_MESSAGE)

    try:
        decoded = _decode_prefixed_key(value) or _decode_unprefixed_key(value)
    except (binascii.Error, ValueError) as e:
        raise CredentialEncryptionError(
            "SEARCH_PROVIDER_CREDENTIAL_MASTER_KEY must be a valid base64url, "
            "base64, or hex encoded 32-byte key"
        ) from e

    if len(decoded) != _AES_256_KEY_SIZE_BYTES:
        raise CredentialEncryptionError(
            "SEARCH_PROVIDER_CREDENTIAL_MASTER_KEY must decode to 32 bytes"
        )
    return decoded


class SearchProviderCredentialCipher:
    def __init__(
        self,
        *,
        master_key: Optional[str],
        key_id: Optional[str],
        hmac_secret: Optional[str],
    ) -> None:
        self._raw_master_key = master_key or ""
        self._raw_key_id = key_id or ""
        self._raw_hmac_secret = hmac_secret or ""
        self._master_key: Optional[bytes] = None

    @property
    def key_id(self) -> str:
        return self._require_key_id(CredentialEncryptionError)

    def encrypt(
        self,
        *,
        user_id: str,
        provider: str,
        api_key: str,
    ) -> EncryptedCredential:
        normalized_key = api_key.strip()
        if not normalized_key:
            raise CredentialEncryptionError("api_key is required")

        master_key = self._require_master_key(CredentialEncryptionError)
        key_id = self._require_key_id(CredentialEncryptionError)
        nonce = os.urandom(_NONCE_SIZE_BYTES)
        aad = _aad(user_id=user_id, provider=provider)
        ciphertext = AESGCM(master_key).encrypt(
            nonce,
            normalized_key.encode("utf-8"),
            aad,
        )

        return EncryptedCredential(
            encrypted_value=":".join(
                (
                    _CIPHERTEXT_VERSION,
                    _base64url_encode(nonce),
                    _base64url_encode(ciphertext),
                )
            ),
            encryption_key_id=key_id,
            key_fingerprint=self.fingerprint(normalized_key),
            key_prefix4=normalized_key[:4],
            key_last4=normalized_key[-4:],
        )

    def decrypt(
        self,
        *,
        user_id: str,
        provider: str,
        encrypted_api_key: str,
        encryption_key_id: str,
    ) -> str:
        master_key = self._require_master_key(CredentialDecryptionError)
        key_id = self._require_key_id(CredentialDecryptionError)
        if encryption_key_id != key_id:
            raise CredentialDecryptionError("unsupported encryption key id")

        try:
            version, nonce_text, ciphertext_text = encrypted_api_key.split(":", 2)
        except ValueError as e:
            raise CredentialDecryptionError("invalid encrypted api key format") from e

        if version != _CIPHERTEXT_VERSION:
            raise CredentialDecryptionError("unsupported encrypted api key version")

        try:
            nonce = _base64url_decode(nonce_text)
            ciphertext = _base64url_decode(ciphertext_text)
            plaintext = AESGCM(master_key).decrypt(
                nonce,
                ciphertext,
                _aad(user_id=user_id, provider=provider),
            )
        except (binascii.Error, ValueError, InvalidTag) as e:
            raise CredentialDecryptionError("failed to decrypt api key") from e

        return plaintext.decode("utf-8")

    def fingerprint(self, api_key: str) -> str:
        hmac_secret = self._require_hmac_secret(CredentialEncryptionError)
        return hmac.new(
            hmac_secret,
            api_key.strip().encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _require_master_key(
        self,
        error_type: type[CredentialEncryptionError] | type[CredentialDecryptionError],
    ) -> bytes:
        if self._master_key is None:
            try:
                self._master_key = decode_master_key(self._raw_master_key)
            except CredentialEncryptionError as e:
                raise error_type(str(e)) from e
        return self._master_key

    def _require_key_id(
        self,
        error_type: type[CredentialEncryptionError] | type[CredentialDecryptionError],
    ) -> str:
        value = self._raw_key_id.strip()
        if not value:
            raise error_type(
                "SEARCH_PROVIDER_CREDENTIAL_KEY_ID is required when using stored "
                "custom search provider credentials."
            )
        return value

    def _require_hmac_secret(
        self,
        error_type: type[CredentialEncryptionError] | type[CredentialDecryptionError],
    ) -> bytes:
        value = self._raw_hmac_secret.strip()
        if not value:
            raise error_type(
                "SEARCH_PROVIDER_CREDENTIAL_HMAC_SECRET is required when using "
                "stored custom search provider credentials."
            )
        return value.encode("utf-8")


def _aad(*, user_id: str, provider: str) -> bytes:
    return f"{user_id}:{provider}".encode("utf-8")


def _decode_prefixed_key(value: str) -> Optional[bytes]:
    if value.startswith("base64url:"):
        return _base64url_decode(value.removeprefix("base64url:"))
    if value.startswith("base64:"):
        return base64.b64decode(value.removeprefix("base64:"), validate=True)
    if value.startswith("hex:"):
        return bytes.fromhex(value.removeprefix("hex:"))
    return None


def _decode_unprefixed_key(value: str) -> bytes:
    if len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value):
        return bytes.fromhex(value)
    return _base64url_decode(value)


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)
