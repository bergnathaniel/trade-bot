import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tradebot import data
from tradebot.data import Candle, DataError, load_candles, read_csv, synthetic, to_product_id, write_csv


class TestProductId(unittest.TestCase):
    def test_stablecoin_quotes_map_to_usd(self):
        self.assertEqual(to_product_id("BTCUSDT"), "BTC-USD")
        self.assertEqual(to_product_id("ETHUSDC"), "ETH-USD")

    def test_other_quotes_are_kept(self):
        self.assertEqual(to_product_id("ETHBTC"), "ETH-BTC")
        self.assertEqual(to_product_id("BTCEUR"), "BTC-EUR")

    def test_hyphenated_passes_through(self):
        self.assertEqual(to_product_id("sol-usd"), "SOL-USD")

    def test_unmappable_symbol(self):
        with self.assertRaises(DataError):
            to_product_id("WEIRD")


class TestCoinbaseParsing(unittest.TestCase):
    def test_rows_are_reordered_and_remapped(self):
        # [time_s, low, high, open, close, volume], newest first
        payload = [[200, 9.0, 12.0, 10.0, 11.0, 3.0], [100, 8.0, 11.0, 9.0, 10.0, 2.0]]
        with mock.patch.object(data, "_get_json", return_value=payload):
            candles = data.fetch_coinbase("BTCUSDT", "1h")
        self.assertEqual([c.open_time for c in candles], [100_000, 200_000])
        self.assertEqual(candles[0], Candle(100_000, 9.0, 11.0, 8.0, 10.0, 2.0))

    def test_unsupported_interval(self):
        with self.assertRaises(DataError):
            data.fetch_coinbase("BTCUSDT", "3m")

    def test_error_body_is_reported(self):
        with mock.patch.object(data, "_get_json", return_value={"message": "NotFound"}):
            with self.assertRaises(DataError):
                data.fetch_coinbase("BTCUSDT", "1h")


class TestAutoSource(unittest.TestCase):
    def test_falls_through_to_the_first_reachable_feed(self):
        expected = synthetic(5)
        calls = []

        def blocked(symbol, interval, limit, *a, **k):
            calls.append("blocked")
            raise DataError("HTTP 451 (region blocked)")

        def works(symbol, interval, limit, *a, **k):
            calls.append("works")
            return expected

        with mock.patch.dict(data._FETCHERS, {"binance": blocked, "binance_us": works}):
            candles = load_candles("auto", "BTCUSDT", "1h", 5)
        self.assertEqual(candles, expected)
        self.assertEqual(calls, ["blocked", "works"])

    def test_all_sources_failing_names_each_one(self):
        def blocked(symbol, interval, limit, *a, **k):
            raise DataError("nope")

        with mock.patch.dict(data._FETCHERS, dict.fromkeys(data.AUTO_SOURCES, blocked)):
            with self.assertRaises(DataError) as ctx:
                load_candles("auto", "BTCUSDT", "1h", 5)
        for name in data.AUTO_SOURCES:
            self.assertIn(name, str(ctx.exception))

    def test_unknown_source(self):
        with self.assertRaises(DataError):
            load_candles("myexchange", "BTCUSDT", "1h", 5)


class TestCsv(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.csv"
            original = synthetic(20)
            write_csv(path, original)
            self.assertEqual(read_csv(path), original)

    def test_missing_file_raises_data_error(self):
        with self.assertRaises(DataError):
            read_csv("/nonexistent/candles.csv")

    def test_malformed_file_raises_data_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.csv"
            path.write_text("open_time,open,high,low,close\n1,not-a-number,2,3,4\n")
            with self.assertRaises(DataError):
                read_csv(path)


if __name__ == "__main__":
    unittest.main()
