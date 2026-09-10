import unittest

from tradebot import backtest, strategy
from tradebot.config import Config
from tradebot.data import Candle, synthetic
from tradebot.portfolio import Position


def candles_from(closes):
    return [Candle(i * 60_000, c, c * 1.01, c * 0.99, c, 1.0) for i, c in enumerate(closes)]


class TestStrategy(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(fast_period=3, slow_period=5, rsi_period=3, atr_period=3, starting_cash=1000.0)

    def test_holds_during_warmup(self):
        candles = candles_from([100.0] * 10)
        ind = strategy.compute(candles, self.cfg)
        self.assertEqual(strategy.decide(candles, ind, 1, self.cfg, None).action, "hold")

    def test_buys_on_golden_cross(self):
        closes = [100, 99, 98, 97, 96, 95, 94, 93] + [95, 99, 104, 110, 117, 125]
        candles = candles_from([float(c) for c in closes])
        cfg = Config(fast_period=3, slow_period=5, rsi_period=3, atr_period=3, rsi_max_entry=100.0)
        ind = strategy.compute(candles, cfg)
        actions = [strategy.decide(candles, ind, i, cfg, None).action for i in range(len(candles))]
        self.assertIn("buy", actions)

    def test_rsi_filter_blocks_overbought_entry(self):
        closes = [100, 99, 98, 97, 96, 95, 94, 93] + [95, 99, 104, 110, 117, 125]
        candles = candles_from([float(c) for c in closes])
        permissive = Config(fast_period=3, slow_period=5, rsi_period=3, atr_period=3, rsi_max_entry=100.0)
        strict = Config(fast_period=3, slow_period=5, rsi_period=3, atr_period=3, rsi_max_entry=1.0)
        ind = strategy.compute(candles, permissive)
        buys = lambda cfg: sum(
            strategy.decide(candles, ind, i, cfg, None).action == "buy" for i in range(len(candles))
        )
        self.assertGreater(buys(permissive), 0)
        self.assertEqual(buys(strict), 0)

    def test_sells_when_price_breaks_the_stop(self):
        candles = candles_from([float(100 + i) for i in range(20)])
        ind = strategy.compute(candles, self.cfg)
        position = Position(qty=1.0, entry_price=100.0, entry_time=0, stop=1e9)
        self.assertEqual(strategy.decide(candles, ind, 15, self.cfg, position).action, "sell")

    def test_trailing_stop_only_ratchets_up(self):
        position = Position(qty=1.0, entry_price=100.0, entry_time=0, stop=90.0, peak=100.0)
        cfg = Config(atr_stop_mult=1.0)
        strategy.update_trailing_stop(position, 120.0, 5.0, cfg)
        self.assertAlmostEqual(position.stop, 115.0)
        strategy.update_trailing_stop(position, 100.0, 5.0, cfg)
        self.assertAlmostEqual(position.stop, 115.0)  # never loosened


class TestBacktest(unittest.TestCase):
    def test_flat_market_leaves_capital_untouched(self):
        cfg = Config(fast_period=3, slow_period=5, rsi_period=3, atr_period=3, starting_cash=1000.0)
        result = backtest.run(candles_from([100.0] * 60), cfg)
        self.assertEqual(result.closed_trades, [])
        self.assertAlmostEqual(result.end_equity, 1000.0)

    def test_short_series_is_a_no_op(self):
        result = backtest.run(candles_from([100.0] * 5), Config())
        self.assertEqual(result.total_return, 0.0)
        self.assertEqual(result.trades, [])

    def test_synthetic_run_is_internally_consistent(self):
        cfg = Config(starting_cash=1000.0)
        result = backtest.run(synthetic(600), cfg)
        self.assertGreater(len(result.equity_curve), 0)
        self.assertLessEqual(result.max_drawdown, 0.0)
        self.assertAlmostEqual(
            result.end_equity,
            cfg.starting_cash + sum(t.pnl for t in result.closed_trades),
            places=6,
        )
        self.assertTrue(0.0 <= result.win_rate <= 1.0)

    def test_fees_make_a_costly_run_worse(self):
        cheap = backtest.run(synthetic(600), Config(fee_rate=0.0, slippage_rate=0.0))
        dear = backtest.run(synthetic(600), Config(fee_rate=0.01, slippage_rate=0.01))
        self.assertGreater(cheap.end_equity, dear.end_equity)


if __name__ == "__main__":
    unittest.main()
