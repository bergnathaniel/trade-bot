"""Candle loading from public exchange endpoints, a CSV file, or synthetic data.

None of these need an API key. `source: "auto"` tries the public feeds in turn
and keeps the first that answers, which is what you want when your network or
region blocks one of them (binance.com returns HTTP 451 to US addresses).
"""

from __future__ import annotations

import csv
import json
import logging
import math
import random
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

USER_AGENT = "trade-bot/0.1 (+https://github.com/bergnathaniel/trade-bot)"
BINANCE_URL = "https://api.binance.com/api/v3/klines"
BINANCE_US_URL = "https://api.binance.us/api/v3/klines"
COINBASE_URL = "https://api.exchange.coinbase.com/products/{product}/candles"

#: Candle widths, in seconds, keyed by the Binance-style interval names.
INTERVAL_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400,
}
#: Quote currencies we know how to split off a Binance-style symbol.
QUOTES = ("USDT", "USDC", "USD", "EUR", "GBP", "BTC", "ETH")

AUTO_SOURCES = ("binance", "binance_us", "coinbase")


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
    if source == "csv":
        if not csv_path:
            raise DataError("source=csv requires csv_path")
        return read_csv(csv_path)[-limit:]
    if source == "auto":
        return _first_working(symbol, interval, limit)
    fetcher = _FETCHERS.get(source)
    if fetcher is None:
        raise DataError(f"unknown source: {source!r}")
    return fetcher(symbol, interval, limit)


def _first_working(symbol: str, interval: str, limit: int) -> list[Candle]:
    problems = []
    for name in AUTO_SOURCES:
        try:
            candles = _FETCHERS[name](symbol, interval, limit)
        except DataError as exc:
            log.info("source %s unavailable: %s", name, exc)
            problems.append(f"{name}: {exc}")
            continue
        if candles:
            log.info("using market data from %s", name)
            return candles
        problems.append(f"{name}: empty response")
    raise DataError("no public feed reachable -> " + "; ".join(problems))


# -- fetchers -----------------------------------------------------------------

def fetch_binance(symbol: str, interval: str, limit: int = 500, timeout: float = 15.0) -> list[Candle]:
    return _fetch_klines(BINANCE_URL, symbol, interval, limit, timeout)


def fetch_binance_us(symbol: str, interval: str, limit: int = 500, timeout: float = 15.0) -> list[Candle]:
    return _fetch_klines(BINANCE_US_URL, symbol, interval, limit, timeout)


def _fetch_klines(url: str, symbol: str, interval: str, limit: int, timeout: float) -> list[Candle]:
    query = urllib.parse.urlencode({
        "symbol": symbol.upper().replace("-", ""),
        "interval": interval,
        "limit": max(1, min(limit, 1000)),
    })
    payload = _get_json(f"{url}?{query}", timeout)
    if isinstance(payload, dict):  # binance reports errors as {"code": ..., "msg": ...}
        raise DataError(f"{url} error: {payload.get('msg', payload)}")
    return [
        Candle(int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5]))
        for row in payload
    ]


def fetch_coinbase(symbol: str, interval: str, limit: int = 300, timeout: float = 15.0) -> list[Candle]:
    granularity = INTERVAL_SECONDS.get(interval)
    if granularity is None:
        raise DataError(f"coinbase supports {sorted(INTERVAL_SECONDS)}, not {interval!r}")
    url = COINBASE_URL.format(product=to_product_id(symbol))
    payload = _get_json(f"{url}?{urllib.parse.urlencode({'granularity': granularity})}", timeout)
    if isinstance(payload, dict):
        raise DataError(f"coinbase error: {payload.get('message', payload)}")
    # Rows are [time_s, low, high, open, close, volume], newest first.
    rows = sorted(payload, key=lambda r: r[0])[-limit:]
    return [
        Candle(int(r[0]) * 1000, float(r[3]), float(r[2]), float(r[1]), float(r[4]), float(r[5]))
        for r in rows
    ]


_FETCHERS = {
    "binance": fetch_binance,
    "binance_us": fetch_binance_us,
    "coinbase": fetch_coinbase,
}


def to_product_id(symbol: str) -> str:
    """BTCUSDT -> BTC-USD, the id Coinbase uses. Already-hyphenated ids pass through."""
    if "-" in symbol:
        return symbol.upper()
    upper = symbol.upper()
    for quote in QUOTES:
        if upper.endswith(quote) and len(upper) > len(quote):
            base = upper[: -len(quote)]
            # Coinbase quotes in USD, not the USDT/USDC stablecoins.
            return f"{base}-{'USD' if quote in ('USDT', 'USDC') else quote}"
    raise DataError(f"cannot map {symbol!r} to a Coinbase product id; use e.g. BTC-USD")


def _get_json(url: str, timeout: float):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        hint = " (region blocked)" if exc.code == 451 else ""
        raise DataError(f"HTTP {exc.code}{hint} from {urllib.parse.urlsplit(url).netloc}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        raise DataError(f"fetch failed for {urllib.parse.urlsplit(url).netloc}: {exc}") from exc


# -- files and fixtures -------------------------------------------------------

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
