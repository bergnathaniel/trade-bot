import unittest

from tradebot.solana import base58, ed25519
from tradebot.solana.keypair import KeyError_, Keypair
from tradebot.solana.transaction import Transaction, decode_shortvec, encode_shortvec

# RFC 8032, section 7.1
VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
     "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
     "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
]


class TestBase58(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(base58.encode(b"hello world"), "StV1DL6CwTryKyV")
        self.assertEqual(base58.decode("StV1DL6CwTryKyV"), b"hello world")

    def test_leading_zero_bytes_become_ones(self):
        self.assertEqual(base58.encode(b"\x00\x00\x01"), "112")
        self.assertEqual(base58.decode("112"), b"\x00\x00\x01")

    def test_round_trip_random_lengths(self):
        for n in range(0, 70, 7):
            raw = bytes(range(n))
            self.assertEqual(base58.decode(base58.encode(raw)), raw)

    def test_invalid_character(self):
        with self.assertRaises(ValueError):
            base58.decode("0OIl")


class TestEd25519(unittest.TestCase):
    def test_rfc8032_vectors(self):
        for seed_hex, pub_hex, msg_hex, sig_hex in VECTORS:
            seed = bytes.fromhex(seed_hex)
            message = bytes.fromhex(msg_hex)
            self.assertEqual(ed25519.public_key(seed).hex(), pub_hex)
            self.assertEqual(ed25519.sign(message, seed).hex(), sig_hex)
            self.assertTrue(ed25519.verify(message, bytes.fromhex(sig_hex), bytes.fromhex(pub_hex)))

    def test_tampered_message_fails_verification(self):
        seed = bytes.fromhex(VECTORS[0][0])
        signature = ed25519.sign(b"buy", seed)
        self.assertTrue(ed25519.verify(b"buy", signature, ed25519.public_key(seed)))
        self.assertFalse(ed25519.verify(b"sell", signature, ed25519.public_key(seed)))

    def test_malformed_inputs_are_rejected(self):
        self.assertFalse(ed25519.verify(b"x", b"short", bytes(32)))
        with self.assertRaises(ValueError):
            ed25519.sign(b"x", b"too-short")


class TestKeypair(unittest.TestCase):
    def setUp(self):
        self.seed = bytes(range(32))
        self.keypair = Keypair(self.seed)

    def test_accepts_seed_and_phantom_style_secret(self):
        combined = base58.encode(self.seed + self.keypair.pubkey)
        self.assertEqual(Keypair.from_base58(combined).address, self.keypair.address)
        self.assertEqual(Keypair.from_bytes(self.seed).address, self.keypair.address)

    def test_mismatched_public_key_is_rejected(self):
        with self.assertRaises(KeyError_):
            Keypair.from_bytes(self.seed + bytes(32))

    def test_wrong_length_is_rejected(self):
        with self.assertRaises(KeyError_):
            Keypair.from_bytes(b"\x01" * 48)

    def test_secret_never_appears_in_repr_or_pickle(self):
        self.assertNotIn(self.seed.hex(), repr(self.keypair))
        self.assertIn(self.keypair.address, repr(self.keypair))
        with self.assertRaises(KeyError_):
            self.keypair.__reduce__()

    def test_from_env_explains_itself_when_unset(self):
        with self.assertRaises(KeyError_) as ctx:
            Keypair.from_env("DEFINITELY_NOT_SET_12345")
        self.assertIn("DEFINITELY_NOT_SET_12345", str(ctx.exception))


def fake_transaction(fee_payer: bytes, signatures: int = 1) -> bytes:
    """A minimal v0 transaction envelope: zeroed sigs, header, one account."""
    message = bytes([0x80]) + bytes([signatures, 0, 0]) + encode_shortvec(1) + fee_payer
    return encode_shortvec(signatures) + bytes(64) * signatures + message


class TestTransaction(unittest.TestCase):
    def setUp(self):
        self.keypair = Keypair(bytes(range(32)))

    def test_shortvec_round_trip(self):
        for value in (0, 1, 127, 128, 255, 16383, 16384, 65535):
            self.assertEqual(decode_shortvec(encode_shortvec(value))[0], value)

    def test_parses_and_reserialises_unchanged(self):
        raw = fake_transaction(self.keypair.pubkey)
        transaction = Transaction.from_bytes(raw)
        self.assertEqual(transaction.version, "v0")
        self.assertEqual(transaction.required_signatures, 1)
        self.assertEqual(transaction.fee_payer(), self.keypair.pubkey)
        self.assertEqual(transaction.to_bytes(), raw)

    def test_signing_fills_slot_zero_and_verifies(self):
        transaction = Transaction.from_bytes(fake_transaction(self.keypair.pubkey))
        signature = transaction.sign_as_fee_payer(self.keypair)
        self.assertTrue(ed25519.verify(transaction.message, transaction.signatures[0], self.keypair.pubkey))
        self.assertEqual(base58.decode(signature), transaction.signatures[0])

    def test_refuses_to_sign_for_another_wallet(self):
        other = Keypair(bytes(range(1, 33)))
        transaction = Transaction.from_bytes(fake_transaction(other.pubkey))
        with self.assertRaises(ValueError) as ctx:
            transaction.sign_as_fee_payer(self.keypair)
        self.assertIn("refusing to sign", str(ctx.exception))
        self.assertEqual(transaction.signatures[0], bytes(64))  # left untouched

    def test_rejects_malformed_envelopes(self):
        with self.assertRaises(ValueError):
            Transaction.from_bytes(encode_shortvec(0) + b"\x80")     # no signature slots
        with self.assertRaises(ValueError):
            Transaction.from_bytes(encode_shortvec(2) + bytes(64))   # truncated
        with self.assertRaises(ValueError):
            Transaction.from_base64("not base64!")


if __name__ == "__main__":
    unittest.main()
