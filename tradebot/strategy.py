"""Long-only trend strategy: SMA crossover, RSI entry filter, ATR trailing stop.

The strategy never looks past index `i`; the caller is responsible for filling
the order on the *next* candle, which is what backtest.py does.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .data import Candle
from .indicators import atr, rsi, sma
from .portfolio import Position


@dataclass(frozen=True)
class Signal:
    action: str          # "buy", "sell" or "hold"
    reason: str = ""
    stop: float = 0.0    # suggested stop level for a new position


@dataclass
class Indicators:
    fast: list[float | None]
    slow: list[float | None]
    rsi: list[float | None]
    atr: list[float | None]


def compute(candles: list[Candle], cfg: Config) -> Indicators:
    closes = [c.close for c in candles]
    return Indicators(
        fast=sma(closes, cfg.fast_period),
        slow=sma(closes, cfg.slow_period),
        rsi=rsi(closes, cfg.rsi_period),
        atr=atr([c.high for c in candles], [c.low for c in candles], closes, cfg.atr_period),
    )


def warmup(cfg: Config) -> int:
    """First index at which every indicator the strategy needs is defined."""
    return max(cfg.slow_period, cfg.fast_period, cfg.rsi_period + 1, cfg.atr_period + 1)


def decide(candles: list[Candle], ind: Indicators, i: int, cfg: Config, position: Position | None) -> Signal:
    if i < warmup(cfg) or i >= len(candles):
        return Signal("hold", "warmup")
    fast, slow = ind.fast[i], ind.slow[i]
    prev_fast, prev_slow = ind.fast[i - 1], ind.slow[i - 1]
    if None in (fast, slow, prev_fast, prev_slow):
        return Signal("hold", "warmup")

    close = candles[i].close
    band = ind.atr[i] or 0.0

    if position is not None:
        if position.stop and close <= position.stop:
            return Signal("sell", "trailing stop")
        if cfg.take_profit_mult > 0 and band:
            target = position.entry_price + cfg.take_profit_mult * band
            if close >= target:
                return Signal("sell", "take profit")
        if prev_fast >= prev_slow and fast < slow:
            return Signal("sell", "trend flip")
        return Signal("hold", "in trend")

    crossed_up = prev_fast <= prev_slow and fast > slow
    if not crossed_up:
        return Signal("hold", "no cross")
    current_rsi = ind.rsi[i]
    if current_rsi is not None and current_rsi > cfg.rsi_max_entry:
        return Signal("hold", f"rsi {current_rsi:.1f} > {cfg.rsi_max_entry:g}")
    stop = close - cfg.atr_stop_mult * band if band else 0.0
    return Signal("buy", "golden cross", stop)


def update_trailing_stop(position: Position, close: float, band: float | None, cfg: Config) -> None:
    """Ratchet the stop upward as the position moves in our favour."""
    position.peak = max(position.peak, close)
    if not band:
        return
    candidate = position.peak - cfg.atr_stop_mult * band
    position.stop = max(position.stop, candidate)
