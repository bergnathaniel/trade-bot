import unittest

from tradebot.portfolio import Portfolio


class TestPortfolio(unittest.TestCase):
    def setUp(self):
        self.p = Portfolio(cash=1000.0, fee_rate=0.001, slippage_rate=0.0)

    def test_buy_then_sell_records_pnl_net_of_fees(self):
        self.p.buy(100.0, 5.0, time=1)
        self.assertTrue(self.p.is_long)
        self.assertAlmostEqual(self.p.cash, 1000 - 500 - 0.5)
        trade = self.p.sell(110.0, time=2, reason="target")
        self.assertFalse(self.p.is_long)
        # 50 gross, minus 0.55 exit fee and 0.5 entry fee
        self.assertAlmostEqual(trade.pnl, 50 - 0.55 - 0.5)
        self.assertAlmostEqual(self.p.equity(110.0), 1000 + trade.pnl)

    def test_no_pyramiding_and_no_naked_sell(self):
        self.p.buy(100.0, 1.0, time=1)
        self.assertIsNone(self.p.buy(100.0, 1.0, time=2))
        self.p.sell(100.0, time=3)
        self.assertIsNone(self.p.sell(100.0, time=4))

    def test_buy_rejected_when_unaffordable(self):
        self.assertIsNone(self.p.buy(100.0, 50.0, time=1))
        self.assertAlmostEqual(self.p.cash, 1000.0)

    def test_size_for_is_affordable(self):
        qty = self.p.size_for(100.0, 0.25)
        trade = self.p.buy(100.0, qty, time=1)
        self.assertIsNotNone(trade)
        self.assertGreaterEqual(self.p.cash, 0.0)

    def test_slippage_moves_fills_against_us(self):
        p = Portfolio(cash=1000.0, fee_rate=0.0, slippage_rate=0.01)
        buy = p.buy(100.0, 1.0, time=1)
        sell = p.sell(100.0, time=2)
        self.assertAlmostEqual(buy.price, 101.0)
        self.assertAlmostEqual(sell.price, 99.0)
        self.assertLess(sell.pnl, 0)

    def test_round_trip_serialisation(self):
        self.p.buy(100.0, 2.0, time=1)
        restored = Portfolio.from_dict(self.p.to_dict())
        self.assertAlmostEqual(restored.cash, self.p.cash)
        self.assertEqual(restored.position.qty, 2.0)
        self.assertEqual(len(restored.trades), 1)


if __name__ == "__main__":
    unittest.main()
