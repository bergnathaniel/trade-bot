"""Loading a burner wallet key, without ever putting it in the repo."""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import base58, ed25519


class KeyError_(ValueError):
    """Raised when a key cannot be loaded or is in an unexpected format."""


class Keypair:
    """A signing key. `seed` is the 32-byte ed25519 seed; never log or serialise it."""

    __slots__ = ("_seed", "pubkey")

    def __init__(self, seed: bytes) -> None:
        if len(seed) != 32:
            raise KeyError_("ed25519 seed must be 32 bytes")
        self._seed = seed
        self.pubkey = ed25519.public_key(seed)

    @property
    def address(self) -> str:
        return base58.encode(self.pubkey)

    def sign(self, message: bytes) -> bytes:
        return ed25519.sign(message, self._seed, self.pubkey)

    # Keep the secret out of tracebacks, logs and reprs.
    def __repr__(self) -> str:
        return f"<Keypair {self.address}>"

    __str__ = __repr__

    def __reduce__(self):
        raise KeyError_("refusing to pickle a Keypair")

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Keypair":
        """Accept either a 32-byte seed or the 64-byte seed||pubkey layout."""
        if len(raw) == 32:
            return cls(raw)
        if len(raw) == 64:
            keypair = cls(raw[:32])
            if keypair.pubkey != raw[32:]:
                raise KeyError_("secret key does not match its embedded public key")
            return keypair
        raise KeyError_(f"expected 32 or 64 key bytes, got {len(raw)}")

    @classmethod
    def from_base58(cls, text: str) -> "Keypair":
        """Phantom's 'Export Private Key' gives base58 of 64 bytes."""
        try:
            raw = base58.decode(text.strip())
        except ValueError as exc:
            raise KeyError_(f"not valid base58: {exc}") from None
        return cls.from_bytes(raw)

    @classmethod
    def from_json_array(cls, text: str) -> "Keypair":
        """`solana-keygen` writes a JSON array of byte values."""
        try:
            values = json.loads(text)
            raw = bytes(values)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise KeyError_(f"not a JSON byte array: {exc}") from None
        return cls.from_bytes(raw)

    @classmethod
    def load(cls, source: str) -> "Keypair":
        """Load from a JSON key file path or a raw base58 secret."""
        text = source.strip()
        if not text:
            raise KeyError_("empty key source")
        path = Path(text)
        if len(text) < 512 and path.exists():
            if path.stat().st_mode & 0o077:
                raise KeyError_(f"{path} is group/world readable; run: chmod 600 {path}")
            return cls.from_json_array(path.read_text())
        if text.startswith("["):
            return cls.from_json_array(text)
        return cls.from_base58(text)

    @classmethod
    def from_env(cls, var: str = "TRADEBOT_WALLET_KEY") -> "Keypair":
        """The only supported way to give the bot a key: an environment variable."""
        source = os.environ.get(var, "")
        if not source:
            raise KeyError_(
                f"{var} is not set. Put it in an environment file readable only by "
                "the bot's user (chmod 600), never in the repo or in CI secrets."
            )
        return cls.load(source)
