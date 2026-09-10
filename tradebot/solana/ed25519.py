"""Ed25519 signing (RFC 8032), implemented here to keep the bot dependency-free.

This is the reference algorithm, not a hardened library: it is constant-*enough*
for a bot signing its own transactions on a machine you control, but if you are
handling keys for anyone but yourself, use libsodium via PyNaCl instead.
"""

from __future__ import annotations

import hashlib

P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
I = pow(2, (P - 1) // 4, P)
BY = 4 * pow(5, P - 2, P) % P
BX = 15112221349535400772501151409588531511454012693041857206046113283949847762202
B = (BX, BY, 1, BX * BY % P)


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _add(p, q):
    """Twisted-Edwards addition in extended coordinates."""
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % P
    b = (y1 + x1) * (y2 + x2) % P
    c = 2 * t1 * t2 * D % P
    d = 2 * z1 * z2 % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _scalar_mult(p, e):
    result = (0, 1, 1, 0)
    while e > 0:
        if e & 1:
            result = _add(result, p)
        p = _add(p, p)
        e >>= 1
    return result


def _compress(p) -> bytes:
    x, y, z, _ = p
    z_inv = pow(z, P - 2, P)
    x, y = x * z_inv % P, y * z_inv % P
    return ((y | ((x & 1) << 255))).to_bytes(32, "little")


def _decompress(data: bytes):
    y = int.from_bytes(data, "little") & ((1 << 255) - 1)
    sign = data[31] >> 7
    xx = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    x = pow(xx, (P + 3) // 8, P)
    if (x * x - xx) % P != 0:
        x = x * I % P
    if (x * x - xx) % P != 0:
        raise ValueError("point is not on the curve")
    if x & 1 != sign:
        x = P - x
    return (x, y, 1, x * y % P)


def _secret_scalar(seed: bytes) -> tuple[int, bytes]:
    digest = _sha512(seed)
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8       # clear the low 3 bits
    scalar |= 1 << 254             # set the high bit
    return scalar, digest[32:]


def public_key(seed: bytes) -> bytes:
    """Derive the 32-byte public key from a 32-byte seed."""
    if len(seed) != 32:
        raise ValueError("ed25519 seed must be 32 bytes")
    scalar, _ = _secret_scalar(seed)
    return _compress(_scalar_mult(B, scalar))


def sign(message: bytes, seed: bytes, pubkey: bytes | None = None) -> bytes:
    """Return the 64-byte signature of `message` under the key derived from `seed`."""
    if len(seed) != 32:
        raise ValueError("ed25519 seed must be 32 bytes")
    scalar, prefix = _secret_scalar(seed)
    pubkey = pubkey or _compress(_scalar_mult(B, scalar))
    r = int.from_bytes(_sha512(prefix + message), "little") % L
    big_r = _compress(_scalar_mult(B, r))
    k = int.from_bytes(_sha512(big_r + pubkey + message), "little") % L
    s = (r + k * scalar) % L
    return big_r + s.to_bytes(32, "little")


def verify(message: bytes, signature: bytes, pubkey: bytes) -> bool:
    """Verify a signature. Used by the tests and by the pre-flight self-check."""
    if len(signature) != 64 or len(pubkey) != 32:
        return False
    try:
        big_r = _decompress(signature[:32])
        point_a = _decompress(pubkey)
    except ValueError:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= L:
        return False
    k = int.from_bytes(_sha512(signature[:32] + pubkey + message), "little") % L
    left = _scalar_mult(B, s)
    right = _add(big_r, _scalar_mult(point_a, k))
    return _compress(left) == _compress(right)
