import unittest

from tradebot.indicators import atr, ema, rsi, sma


class TestIndicators(unittest.TestCase):
    def test_sma_leading_none_and_values(self):
        out = sma([1, 2, 3, 4, 5], 3)
        self.assertEqual(out[:2], [None, None])
        self.assertAlmostEqual(out[2], 2.0)
        self.assertAlmostEqual(out[4], 4.0)

    def test_sma_rejects_bad_period(self):
        with self.assertRaises(ValueError):
            sma([1, 2, 3], 0)

    def test_ema_seeds_with_sma(self):
        out = ema([1, 2, 3, 4, 5], 3)
        self.assertIsNone(out[1])
        self.assertAlmostEqual(out[2], 2.0)
        self.assertAlmostEqual(out[3], 3.0)

    def test_rsi_is_100_when_only_gains(self):
        out = rsi(list(range(1, 30)), 14)
        self.assertAlmostEqual(out[-1], 100.0)

    def test_rsi_stays_in_range(self):
        values = [10, 11, 10.5, 12, 11.8, 13, 12.2, 12.9, 14, 13.4, 15, 14.2, 16, 15.1, 17, 16.4, 18]
        for value in rsi(values, 14):
            if value is not None:
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 100.0)

    def test_rsi_too_short(self):
        self.assertEqual(rsi([1, 2, 3], 14), [None, None, None])

    def test_atr_positive_and_length_checked(self):
        highs = [i + 1.0 for i in range(30)]
        lows = [i - 1.0 for i in range(30)]
        closes = [float(i) for i in range(30)]
        out = atr(highs, lows, closes, 14)
        self.assertIsNone(out[13])
        self.assertGreater(out[-1], 0)
        with self.assertRaises(ValueError):
            atr(highs, lows[:5], closes, 14)


if __name__ == "__main__":
    unittest.main()
