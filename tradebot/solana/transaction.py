"""Just enough Solana wire format to sign a transaction someone else built.

Jupiter returns a fully-formed (legacy or v0) transaction with zeroed signature
slots. We do not construct instructions ourselves: we parse the envelope, check
the fee payer really is our wallet, sign the message bytes and put the signature
in slot 0.
"""

from __future__ import annotations

import base64

SIGNATURE_LEN = 64
PUBKEY_LEN = 32


def decode_shortvec(data: bytes, offset: int = 0) -> tuple[int, int]:
    """Solana's compact-u16. Returns (value, bytes consumed)."""
    value = 0
    for i in range(3):
        if offset + i >= len(data):
            raise ValueError("truncated compact-u16")
        byte = data[offset + i]
        value |= (byte & 0x7F) << (7 * i)
        if not byte & 0x80:
            return value, i + 1
    raise ValueError("compact-u16 too long")


def encode_shortvec(value: int) -> bytes:
    if value < 0 or value > 0xFFFF:
        raise ValueError("compact-u16 out of range")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


class Transaction:
    """A parsed transaction envelope: signature slots plus the message bytes."""

    def __init__(self, signatures: list[bytes], message: bytes) -> None:
        self.signatures = signatures
        self.message = message

    @classmethod
    def from_base64(cls, encoded: str) -> "Transaction":
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"transaction is not valid base64: {exc}") from None
        return cls.from_bytes(raw)

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Transaction":
        count, consumed = decode_shortvec(raw)
        if count == 0:
            raise ValueError("transaction has no signature slots")
        end = consumed + count * SIGNATURE_LEN
        if end > len(raw):
            raise ValueError("truncated signature array")
        signatures = [raw[consumed + i * SIGNATURE_LEN: consumed + (i + 1) * SIGNATURE_LEN]
                      for i in range(count)]
        message = raw[end:]
        if not message:
            raise ValueError("transaction has no message")
        return cls(signatures, message)

    def to_bytes(self) -> bytes:
        return encode_shortvec(len(self.signatures)) + b"".join(self.signatures) + self.message

    def to_base64(self) -> str:
        return base64.b64encode(self.to_bytes()).decode()

    # -- message inspection ------------------------------------------------
    @property
    def version(self) -> str:
        """'legacy', or 'v0' for a versioned message."""
        first = self.message[0]
        return f"v{first & 0x7F}" if first & 0x80 else "legacy"

    def accounts(self) -> list[bytes]:
        """The static account keys, in order. accounts()[0] is the fee payer."""
        offset = 1 if self.message[0] & 0x80 else 0
        offset += 3  # header: required signatures, readonly signed, readonly unsigned
        count, consumed = decode_shortvec(self.message, offset)
        offset += consumed
        end = offset + count * PUBKEY_LEN
        if end > len(self.message):
            raise ValueError("truncated account list")
        return [self.message[offset + i * PUBKEY_LEN: offset + (i + 1) * PUBKEY_LEN]
                for i in range(count)]

    @property
    def required_signatures(self) -> int:
        offset = 1 if self.message[0] & 0x80 else 0
        return self.message[offset]

    def fee_payer(self) -> bytes:
        return self.accounts()[0]

    # -- signing -----------------------------------------------------------
    def sign_as_fee_payer(self, keypair) -> str:
        """Sign in slot 0 after checking slot 0 is really ours. Returns the signature (base58)."""
        from . import base58 as b58

        payer = self.fee_payer()
        if payer != keypair.pubkey:
            raise ValueError(
                f"refusing to sign: fee payer is {b58.encode(payer)}, wallet is {keypair.address}"
            )
        signature = keypair.sign(self.message)
        self.signatures[0] = signature
        return b58.encode(signature)
