"""Walk a candle series through the strategy and score the result."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import strategy
from .config import Config
from .data import Candle
from .portfolio import Portfolio, Trade


@dataclass
class Result:
    start_equity: float
    end_equity: float
    equity_curve: list[tuple[int, float]] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    buy_and_hold: float = 0.0

    @property
    def total_return(self) -> float:
        return self.end_equity / self.start_equity - 1 if self.start_equity else 0.0

    @property
    def max_drawdown(self) -> float:
        peak = -math.inf
        worst = 0.0
        for _, equity in self.equity_curve:
            peak = max(peak, equity)
            if peak > 0:
                worst = min(worst, equity / peak - 1)
        return worst

    @property
    def closed_trades(self) -> list[Trade]:
        return [t for t in self.trades if t.side == "sell"]

    @property
    def win_rate(self) -> float:
        closed = self.closed_trades
        if not closed:
            return 0.0
        return sum(1 for t in closed if t.pnl > 0) / len(closed)

    @property
    def sharpe(self) -> float:
        """Per-bar Sharpe (rf=0), annualisation left to the caller."""
        returns = []
        for (_, prev), (_, cur) in zip(self.equity_curve, self.equity_curve[1:]):
            if prev > 0:
                returns.append(cur / prev - 1)
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        return mean / math.sqrt(var) if var > 0 else 0.0

    def summary(self) -> str:
        return (
            f"trades: {len(self.closed_trades)}  "
            f"return: {self.total_return * 100:+.2f}%  "
            f"buy&hold: {self.buy_and_hold * 100:+.2f}%  "
            f"max drawdown: {self.max_drawdown * 100:.2f}%  "
            f"win rate: {self.win_rate * 100:.1f}%  "
            f"sharpe/bar: {self.sharpe:.3f}"
        )


def run(candles: list[Candle], cfg: Config) -> Result:
    """Signals are read on bar i and filled at the open of bar i+1 (no lookahead)."""
    portfolio = Portfolio(cfg.starting_cash, cfg.fee_rate, cfg.slippage_rate)
    ind = strategy.compute(candles, cfg)
    result = Result(start_equity=cfg.starting_cash, end_equity=cfg.starting_cash)
    start = strategy.warmup(cfg)
    if len(candles) <= start + 1:
        return result

    for i in range(start, len(candles) - 1):
        candle, next_candle = candles[i], candles[i + 1]
        if portfolio.position is not None:
            strategy.update_trailing_stop(portfolio.position, candle.close, ind.atr[i], cfg)
        signal = strategy.decide(candles, ind, i, cfg, portfolio.position)
        if signal.action == "buy":
            qty = portfolio.size_for(next_candle.open, cfg.risk_fraction)
            trade = portfolio.buy(next_candle.open, qty, next_candle.open_time, signal.reason)
            if trade and signal.stop:
                portfolio.position.stop = signal.stop
        elif signal.action == "sell":
            portfolio.sell(next_candle.open, next_candle.open_time, signal.reason)
        result.equity_curve.append((next_candle.open_time, portfolio.equity(next_candle.close)))

    last = candles[-1]
    if portfolio.position is not None:
        portfolio.sell(last.close, last.open_time, "end of data")
    result.end_equity = portfolio.equity(last.close)
    result.trades = portfolio.trades
    first_close = candles[start].close
    result.buy_and_hold = last.close / first_close - 1 if first_close else 0.0
    return result
