import io
import json
import unittest
from contextlib import redirect_stdout

from tradebot.cli import build_parser, main
from tradebot.config import Config


class TestCli(unittest.TestCase):
    def test_shared_flags_work_on_either_side_of_the_subcommand(self):
        parser = build_parser()
        before = parser.parse_args(["--symbol", "ETHUSDT", "backtest", "--synthetic", "50"])
        after = parser.parse_args(["backtest", "--synthetic", "50", "--symbol", "ETHUSDT"])
        self.assertEqual(before.symbol, after.symbol)
        self.assertEqual(before.command, after.command)

    def test_unset_shared_flags_are_absent_not_none(self):
        args = build_parser().parse_args(["backtest", "--synthetic", "50"])
        self.assertFalse(hasattr(args, "symbol"))

    def test_backtest_json_output(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(main(["backtest", "--synthetic", "400", "--json"]), 0)
        report = json.loads(buffer.getvalue())
        self.assertEqual(report["candles"], 400)
        self.assertIn("buy_and_hold", report)

    def test_show_redacts_secrets(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            main(["show"])
        printed = json.loads(buffer.getvalue())
        self.assertEqual(printed["source"], Config().source)
        self.assertNotIn("s3cret", buffer.getvalue())

    def test_source_override_reaches_the_config(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            main(["show", "--source", "coinbase", "--symbol", "ETHUSDT"])
        printed = json.loads(buffer.getvalue())
        self.assertEqual(printed["source"], "coinbase")
        self.assertEqual(printed["symbol"], "ETHUSDT")


if __name__ == "__main__":
    unittest.main()
