import json
import tempfile
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from tradebot.config import Config
from tradebot.data import synthetic, write_csv
from tradebot.engine import Engine
from tradebot.notify import Notifier
from tradebot.server import make_handler


class RecordingNotifier(Notifier):
    def __init__(self):
        super().__init__()
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        csv_path = Path(self.tmp.name) / "candles.csv"
        write_csv(csv_path, synthetic(600))
        self.cfg = Config(
            source="csv", csv_path=str(csv_path), state_dir=self.tmp.name,
            starting_cash=1000.0, symbol="TEST", interval="1h",
        )

    def test_live_mode_is_refused(self):
        with self.assertRaises(NotImplementedError):
            Engine(Config(live=True, state_dir=self.tmp.name))

    def test_step_persists_and_is_idempotent_within_a_candle(self):
        engine = Engine(self.cfg, RecordingNotifier())
        engine.step()
        self.assertTrue(self.cfg.state_path.exists())
        second = engine.step()
        self.assertIn("no new candle", second)

    def test_state_survives_a_restart(self):
        engine = Engine(self.cfg, RecordingNotifier())
        engine.step()
        engine.state.portfolio.cash = 123.45
        engine.save_state()
        self.assertAlmostEqual(Engine(self.cfg).state.portfolio.cash, 123.45)

    def test_corrupt_state_falls_back_to_a_fresh_account(self):
        self.cfg.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.state_path.write_text("{not json")
        self.assertAlmostEqual(Engine(self.cfg).state.portfolio.cash, 1000.0)

    def test_missing_data_is_reported_not_raised(self):
        cfg = Config(source="csv", csv_path=str(Path(self.tmp.name) / "nope.csv"), state_dir=self.tmp.name)
        engine = Engine(cfg)
        self.assertIn("data error", engine.step())

    def test_snapshot_shape(self):
        engine = Engine(self.cfg, RecordingNotifier())
        engine.step()
        snap = engine.snapshot()
        self.assertEqual(snap["mode"], "paper")
        self.assertEqual(snap["symbol"], "TEST")
        self.assertIn("equity", snap)
        json.dumps(snap)  # must be serialisable for the dashboard


class TestServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        csv_path = Path(self.tmp.name) / "candles.csv"
        write_csv(csv_path, synthetic(300))
        cfg = Config(source="csv", csv_path=str(csv_path), state_dir=self.tmp.name, dashboard_token="s3cret")
        engine = Engine(cfg)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(engine, "s3cret"))
        Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)
        self.port = self.httpd.server_address[1]

    def get(self, path):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response.status, body

    def test_health_needs_no_token(self):
        self.assertEqual(self.get("/healthz")[0], 200)

    def test_token_is_required(self):
        self.assertEqual(self.get("/")[0], 403)
        self.assertEqual(self.get("/?token=wrong")[0], 403)
        self.assertEqual(self.get("/?token=s3cret")[0], 200)

    def test_api_returns_json(self):
        status, body = self.get("/api/state?token=s3cret")
        self.assertEqual(status, 200)
        self.assertIn("equity", json.loads(body))

    def test_unknown_path(self):
        self.assertEqual(self.get("/nope?token=s3cret")[0], 404)


if __name__ == "__main__":
    unittest.main()
