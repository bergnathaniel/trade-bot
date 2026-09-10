import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tradebot import guards
from tradebot.config import Config
from tradebot.execution import LiveExecutor, PaperExecutor, split_pair
from tradebot.guards import DailyLedger, GuardTripped, Limits
from tradebot.portfolio import Portfolio
from tradebot.solana import jupiter
from tradebot.solana.jupiter import Quote
from tradebot.solana.keypair import Keypair
from tradebot.solana.transaction import Transaction, encode_shortvec

SOL_MINT = jupiter.KNOWN_MINTS["SOL"][0]
USDC_MINT = jupiter.KNOWN_MINTS["USDC"][0]


def a_quote(out=1_000_000, min_out=995_000, impact=0.0001, in_amount=25_000_000):
    return Quote(USDC_MINT, SOL_MINT, in_amount, out, min_out, impact, {"fake": True})


class TestGuards(unittest.TestCase):
    def setUp(self):
        self.limits = Limits(max_trade_usd=25.0, daily_loss_limit_usd=50.0, max_trades_per_day=3,
                             max_slippage_bps=100, max_price_impact_pct=1.0, min_sol_reserve=0.02,
                             allowed_mints={SOL_MINT, USDC_MINT})

    def test_kill_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            guards.check_kill_switch(tmp)  # absent: fine
            (Path(tmp) / guards.HALT_FILENAME).touch()
            with self.assertRaises(GuardTripped):
                guards.check_kill_switch(tmp)

    def test_notional_cap(self):
        guards.check_notional(25.0, self.limits)
        with self.assertRaises(GuardTripped):
            guards.check_notional(25.01, self.limits)
        with self.assertRaises(GuardTripped):
            guards.check_notional(0.0, self.limits)

    def test_daily_trade_cap_and_loss_limit(self):
        ledger = DailyLedger()
        for _ in range(3):
            ledger.record(-1.0)
        with self.assertRaises(GuardTripped):
            guards.check_daily(ledger, self.limits)

        losing = DailyLedger(day=ledger.day, realised_usd=-50.0, trades=1)
        with self.assertRaises(GuardTripped) as ctx:
            guards.check_daily(losing, self.limits)
        self.assertIn("daily loss limit", str(ctx.exception))

    def test_ledger_resets_on_a_new_utc_day(self):
        ledger = DailyLedger(day="1999-01-01", realised_usd=-100.0, trades=9)
        ledger.roll()
        self.assertEqual(ledger.realised_usd, 0.0)
        self.assertEqual(ledger.trades, 0)
        guards.check_daily(ledger, self.limits)  # no longer blocked

    def test_mint_allowlist(self):
        guards.check_mints(self.limits, SOL_MINT, USDC_MINT)
        with self.assertRaises(GuardTripped):
            guards.check_mints(self.limits, "SomeRandomMint1111111111111111111111111111")
        with self.assertRaises(GuardTripped):
            guards.check_mints(Limits(), SOL_MINT)  # empty allowlist refuses everything

    def test_quote_slippage_and_impact(self):
        guards.check_quote(a_quote(), self.limits)
        with self.assertRaises(GuardTripped) as ctx:
            guards.check_quote(a_quote(min_out=900_000), self.limits)  # 1000 bps
        self.assertIn("slippage", str(ctx.exception))
        with self.assertRaises(GuardTripped) as ctx:
            guards.check_quote(a_quote(impact=0.05), self.limits)      # 0.05 == 5%
        self.assertIn("price impact", str(ctx.exception))

    def test_fee_reserve(self):
        guards.check_fee_reserve(0.05, self.limits)
        with self.assertRaises(GuardTripped):
            guards.check_fee_reserve(0.01, self.limits)
        with self.assertRaises(GuardTripped):
            guards.check_fee_reserve(0.05, self.limits, spending_sol=0.04)


class TestPairSplitting(unittest.TestCase):
    def test_known_pairs(self):
        self.assertEqual(split_pair("SOLUSDC"), ("SOL", "USDC"))
        self.assertEqual(split_pair("sol-usdt"), ("SOL", "USDT"))

    def test_unknown_pair_is_refused(self):
        for symbol in ("BTCUSDT", "DOGEUSDC", "SOL"):
            with self.assertRaises(ValueError):
                split_pair(symbol)


class FakeRpc:
    """Stands in for a Solana node. Records what it was asked to broadcast."""

    def __init__(self, lamports=1_000_000_000, token_balance=100.0, sim_error=None):
        self.lamports = lamports
        self.token_balance = token_balance
        self.sim_error = sim_error
        self.sent = []
        self.confirmed = []

    def get_balance(self, address):
        return self.lamports

    def get_token_balance(self, owner, mint):
        return self.token_balance

    def simulate(self, signed):
        return {"err": self.sim_error}

    def send(self, signed, skip_preflight=False):
        self.sent.append(signed)
        return "SIGNATURE"

    def confirm(self, signature, timeout=90.0):
        self.confirmed.append(signature)
        return {"confirmationStatus": "confirmed"}


def swap_transaction(keypair) -> str:
    message = bytes([0x80, 1, 0, 0]) + encode_shortvec(1) + keypair.pubkey
    return Transaction(list([bytes(64)]), message).to_base64()


class TestLiveExecutor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.keypair = Keypair(bytes(range(32)))
        self.cfg = Config(symbol="SOLUSDC", live=True, dry_run=False, state_dir=self.tmp.name,
                          max_trade_usd=25.0, risk_fraction=0.25, starting_cash=100.0)
        self.rpc = FakeRpc()
        self.portfolio = Portfolio(100.0)
        self.executor = LiveExecutor(self.cfg, self.portfolio, self.keypair, rpc=self.rpc)

    def patched(self, quote=None):
        return mock.patch.multiple(
            jupiter,
            get_quote=mock.DEFAULT if quote is None else mock.Mock(return_value=quote),
            build_swap=mock.Mock(return_value=swap_transaction(self.keypair)),
        )

    def test_buy_broadcasts_once_and_records_the_position(self):
        quote = a_quote(out=jupiter.to_base_units(0.24, 9), min_out=jupiter.to_base_units(0.238, 9),
                        in_amount=jupiter.to_base_units(25.0, 6))
        with mock.patch.object(jupiter, "get_quote", return_value=quote), \
             mock.patch.object(jupiter, "build_swap", return_value=swap_transaction(self.keypair)):
            trade = self.executor.buy(100.0, 1, "golden cross")
        self.assertIsNotNone(trade)
        self.assertEqual(len(self.rpc.sent), 1)
        self.assertEqual(self.rpc.confirmed, ["SIGNATURE"])
        self.assertAlmostEqual(self.portfolio.position.qty, 0.24)
        self.assertAlmostEqual(trade.price, 25.0 / 0.24, places=4)

    def test_dry_run_signs_and_simulates_but_never_sends(self):
        self.cfg.dry_run = True
        quote = a_quote(out=jupiter.to_base_units(0.24, 9), min_out=jupiter.to_base_units(0.238, 9))
        with mock.patch.object(jupiter, "get_quote", return_value=quote), \
             mock.patch.object(jupiter, "build_swap", return_value=swap_transaction(self.keypair)):
            trade = self.executor.buy(100.0, 1, "golden cross")
        self.assertIsNone(trade)
        self.assertEqual(self.rpc.sent, [])
        self.assertIsNone(self.portfolio.position)

    def test_kill_switch_stops_the_trade_before_any_quote(self):
        (Path(self.tmp.name) / guards.HALT_FILENAME).touch()
        with mock.patch.object(jupiter, "get_quote") as quote_call:
            self.assertIsNone(self.executor.buy(100.0, 1, "golden cross"))
        quote_call.assert_not_called()
        self.assertEqual(self.rpc.sent, [])

    def test_size_is_capped_at_max_trade_usd(self):
        self.rpc.token_balance = 10_000.0   # plenty of USDC available
        self.cfg.risk_fraction = 1.0        # strategy would spend all of it
        quote = a_quote(out=jupiter.to_base_units(0.24, 9), min_out=jupiter.to_base_units(0.238, 9))
        with mock.patch.object(jupiter, "get_quote", return_value=quote) as quote_call, \
             mock.patch.object(jupiter, "build_swap", return_value=swap_transaction(self.keypair)):
            self.executor.buy(100.0, 1, "golden cross")
        requested_usd = jupiter.from_base_units(quote_call.call_args[0][2], 6)
        self.assertAlmostEqual(requested_usd, 25.0)  # capped, not 10,000

    def test_failed_simulation_never_broadcasts(self):
        self.rpc.sim_error = {"InstructionError": [0, "Custom"]}
        quote = a_quote(out=jupiter.to_base_units(0.24, 9), min_out=jupiter.to_base_units(0.238, 9))
        with mock.patch.object(jupiter, "get_quote", return_value=quote), \
             mock.patch.object(jupiter, "build_swap", return_value=swap_transaction(self.keypair)):
            self.assertIsNone(self.executor.buy(100.0, 1, "golden cross"))
        self.assertEqual(self.rpc.sent, [])

    def test_transaction_for_another_wallet_is_never_signed_or_sent(self):
        stranger = Keypair(bytes(range(1, 33)))
        quote = a_quote(out=jupiter.to_base_units(0.24, 9), min_out=jupiter.to_base_units(0.238, 9))
        with mock.patch.object(jupiter, "get_quote", return_value=quote), \
             mock.patch.object(jupiter, "build_swap", return_value=swap_transaction(stranger)):
            self.assertIsNone(self.executor.buy(100.0, 1, "golden cross"))
        self.assertEqual(self.rpc.sent, [])

    def test_low_sol_balance_blocks_trading(self):
        self.rpc.lamports = 1_000_000  # 0.001 SOL, below the reserve
        with mock.patch.object(jupiter, "get_quote") as quote_call:
            self.assertIsNone(self.executor.buy(100.0, 1, "golden cross"))
        quote_call.assert_not_called()

    def test_sell_with_no_position_does_nothing(self):
        self.assertIsNone(self.executor.sell(100.0, 1, "stop"))
        self.assertEqual(self.rpc.sent, [])


class TestPaperExecutor(unittest.TestCase):
    def test_paper_path_is_unchanged(self):
        cfg = Config(starting_cash=1000.0, risk_fraction=0.25)
        portfolio = Portfolio(1000.0)
        executor = PaperExecutor(cfg, portfolio)
        trade = executor.buy(100.0, 1, "golden cross")
        self.assertIsNotNone(trade)
        self.assertTrue(portfolio.is_long)
        self.assertIsNotNone(executor.sell(110.0, 2, "take profit"))
        self.assertFalse(portfolio.is_long)


if __name__ == "__main__":
    unittest.main()
