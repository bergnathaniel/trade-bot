"""Candle loading: Binance's public REST endpoint, a CSV file, or synthetic data."""

from __future__ import annotations

import csv
import json
import math
import random
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BINANCE_URL = "https://api.binance.com/api/v3/klines"
USER_AGENT = "trade-bot/0.1 (+https://github.com/bergnathaniel/trade-bot)"


@dataclass(frozen=True)
class Candle:
    open_time: int  # milliseconds since epoch
    open: float
    high: float
    low: float
    close: float
    volume: float


class DataError(RuntimeError):
    """Raised when candles cannot be loaded from the configured source."""


def load_candles(source: str, symbol: str, interval: str, limit: int, csv_path: str = "") -> list[Candle]:
    if source == "binance":
        return fetch_binance(symbol, interval, limit)
    if source == "csv":
        if not csv_path:
            raise DataError("source=csv requires csv_path")
        return read_csv(csv_path)[-limit:]
    raise DataError(f"unknown source: {source!r}")


def fetch_binance(symbol: str, interval: str, limit: int = 500, timeout: float = 15.0) -> list[Candle]:
    query = urllib.parse.urlencode({
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": max(1, min(limit, 1000)),
    })
    request = urllib.request.Request(f"{BINANCE_URL}?{query}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise DataError(f"binance fetch failed: {exc}") from exc
    return [
        Candle(int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5]))
        for row in payload
    ]


def read_csv(path: str | Path) -> list[Candle]:
    """Read candles from a CSV with open_time,open,high,low,close,volume columns."""
    rows: list[Candle] = []
    try:
        with Path(path).open(newline="") as handle:
            for row in csv.DictReader(handle):
                rows.append(Candle(
                    int(float(row["open_time"])),
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row.get("volume", 0.0) or 0.0),
                ))
    except OSError as exc:
        raise DataError(f"cannot read candles from {path}: {exc}") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise DataError(f"malformed candle CSV {path}: {exc}") from exc
    rows.sort(key=lambda c: c.open_time)
    if not rows:
        raise DataError(f"no candles in {path}")
    return rows


def write_csv(path: str | Path, candles: list[Candle]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["open_time", "open", "high", "low", "close", "volume"])
        for c in candles:
            writer.writerow([c.open_time, c.open, c.high, c.low, c.close, c.volume])


def synthetic(n: int = 400, start: float = 100.0, seed: int = 7, step_ms: int = 3_600_000) -> list[Candle]:
    """A deterministic trending-then-mean-reverting series, for tests and demos."""
    rng = random.Random(seed)
    candles: list[Candle] = []
    price = start
    for i in range(n):
        drift = 0.35 * math.sin(i / 40.0)
        price = max(1.0, price * (1 + (drift + rng.gauss(0, 1)) / 200.0))
        high = price * (1 + abs(rng.gauss(0, 1)) / 400.0)
        low = price * (1 - abs(rng.gauss(0, 1)) / 400.0)
        open_ = candles[-1].close if candles else price
        candles.append(Candle(i * step_ms, open_, max(high, open_, price), min(low, open_, price), price, 100.0))
    return candles
