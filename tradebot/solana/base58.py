"""Bitcoin-alphabet base58, which is how Solana writes keys and signatures."""

from __future__ import annotations

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {ch: i for i, ch in enumerate(ALPHABET)}


def encode(raw: bytes) -> str:
    leading_zeros = len(raw) - len(raw.lstrip(b"\0"))
    number = int.from_bytes(raw, "big")
    digits = ""
    while number:
        number, remainder = divmod(number, 58)
        digits = ALPHABET[remainder] + digits
    return "1" * leading_zeros + (digits or ("" if raw else ""))


def decode(text: str) -> bytes:
    number = 0
    for char in text:
        try:
            number = number * 58 + _INDEX[char]
        except KeyError:
            raise ValueError(f"invalid base58 character: {char!r}") from None
    leading_ones = len(text) - len(text.lstrip("1"))
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * leading_ones + body
