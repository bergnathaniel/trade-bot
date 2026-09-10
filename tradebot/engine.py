"""The live loop. Paper trading only: no exchange keys, no real orders."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import strategy
from .config import Config
from .data import DataError, load_candles
from .execution import Executor, LiveExecutor, PaperExecutor
from .guards import DailyLedger
from .notify import Notifier
from .portfolio import Portfolio
from .solana.keypair import Keypair

log = logging.getLogger(__name__)


@dataclass
class State:
    portfolio: Portfolio
    last_candle_time: int = 0
    last_price: float = 0.0
    last_checked: float = 0.0
    equity_curve: list[tuple[int, float]] = field(default_factory=list)
    ledger: DailyLedger = field(default_factory=DailyLedger)

    def to_dict(self) -> dict:
        return {
            "portfolio": self.portfolio.to_dict(),
            "last_candle_time": self.last_candle_time,
            "last_price": self.last_price,
            "last_checked": self.last_checked,
            "equity_curve": self.equity_curve[-2000:],
            "ledger": self.ledger.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "State":
        return cls(
            portfolio=Portfolio.from_dict(data["portfolio"]),
            last_candle_time=int(data.get("last_candle_time", 0)),
            last_price=float(data.get("last_price", 0.0)),
            last_checked=float(data.get("last_checked", 0.0)),
            equity_curve=[tuple(p) for p in data.get("equity_curve", [])],
            ledger=DailyLedger.from_dict(data.get("ledger")),
        )


class Engine:
    def __init__(self, cfg: Config, notifier: Notifier | None = None,
                 executor: Executor | None = None) -> None:
        self.cfg = cfg
        self.notifier = notifier or Notifier(cfg.telegram_token, cfg.telegram_chat_id)
        self.state = self.load_state()
        self.executor = executor or self._build_executor()

    def _build_executor(self) -> Executor:
        if not self.cfg.live:
            return PaperExecutor(self.cfg, self.state.portfolio)
        keypair = Keypair.from_env(self.cfg.wallet_key_env)
        require_live_confirmation(keypair.address)
        log.warning(
            "LIVE MODE on %s: wallet %s, max %.2f USD per trade, daily loss limit %.2f USD%s",
            self.cfg.symbol, keypair.address, self.cfg.max_trade_usd,
            self.cfg.daily_loss_limit_usd, " (dry run)" if self.cfg.dry_run else "",
        )
        return LiveExecutor(self.cfg, self.state.portfolio, keypair, ledger=self.state.ledger)

    # -- persistence -----------------------------------------------------
    def load_state(self) -> State:
        path = self.cfg.state_path
        if path.exists():
            try:
                return State.from_dict(json.loads(path.read_text()))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                log.warning("ignoring unreadable state file %s: %s", path, exc)
        return State(Portfolio(self.cfg.starting_cash, self.cfg.fee_rate, self.cfg.slippage_rate))

    def save_state(self) -> None:
        path = self.cfg.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state.to_dict(), indent=2))
        tmp.replace(path)  # atomic, so a crash mid-write cannot corrupt the state

    # -- one iteration ---------------------------------------------------
    def step(self) -> str:
        """Fetch candles, act on the latest *closed* one, persist. Returns a status line."""
        try:
            candles = load_candles(
                self.cfg.source, self.cfg.symbol, self.cfg.interval, self.cfg.lookback, self.cfg.csv_path
            )
        except DataError as exc:
            log.warning("%s", exc)
            return f"data error: {exc}"

        self.state.last_checked = time.time()
        if len(candles) < strategy.warmup(self.cfg) + 2:
            return "not enough history yet"

        # The final candle is still forming, so decide on the one before it.
        closed = candles[:-1]
        i = len(closed) - 1
        price = closed[i].close
        self.state.last_price = price

        if closed[i].open_time == self.state.last_candle_time:
            self.save_state()
            return f"no new candle (price {price:.2f})"
        self.state.last_candle_time = closed[i].open_time

        ind = strategy.compute(closed, self.cfg)
        portfolio = self.state.portfolio
        self.executor.refresh(price)
        if portfolio.position is not None:
            strategy.update_trailing_stop(portfolio.position, price, ind.atr[i], self.cfg)

        signal = strategy.decide(closed, ind, i, self.cfg, portfolio.position)
        status = f"{signal.action} ({signal.reason}) @ {price:.2f}"
        if signal.action == "buy":
            trade = self.executor.buy(price, closed[i].open_time, signal.reason)
            if trade:
                if signal.stop and portfolio.position is not None:
                    portfolio.position.stop = signal.stop
                self.notifier.send(
                    f"BUY {self.cfg.symbol} {trade.qty:.6f} @ {trade.price:.2f} ({signal.reason})"
                )
        elif signal.action == "sell":
            trade = self.executor.sell(price, closed[i].open_time, signal.reason)
            if trade:
                self.notifier.send(
                    f"SELL {self.cfg.symbol} {trade.qty:.6f} @ {trade.price:.2f} "
                    f"({signal.reason}) pnl {trade.pnl:+.2f}"
                )

        self.state.equity_curve.append((closed[i].open_time, self.executor.equity(price)))
        self.save_state()
        log.info("%s | equity %.2f", status, self.executor.equity(price))
        return status

    def run_forever(self) -> None:
        log.info("%s %s %s every %ss", self.mode, self.cfg.symbol, self.cfg.interval, self.cfg.poll_seconds)
        self.notifier.send(f"trade-bot started ({self.mode}) on {self.cfg.symbol} {self.cfg.interval}")
        while True:
            try:
                self.step()
            except Exception:  # a bad tick must not kill a long-running loop
                log.exception("step failed")
            time.sleep(max(5, self.cfg.poll_seconds))

    # -- read model for the dashboard ------------------------------------
    @property
    def mode(self) -> str:
        if not self.cfg.live:
            return "paper"
        return "live (dry run)" if self.cfg.dry_run else "LIVE"

    def snapshot(self) -> dict:
        portfolio = self.state.portfolio
        price = self.state.last_price
        equity = self.executor.equity(price)
        position = None
        if portfolio.position:
            p = portfolio.position
            position = {
                "qty": p.qty,
                "entry_price": p.entry_price,
                "stop": p.stop,
                "unrealised": (price - p.entry_price) * p.qty if price else 0.0,
            }
        return {
            "symbol": self.cfg.symbol,
            "interval": self.cfg.interval,
            "mode": self.mode,
            "price": price,
            "equity": equity,
            "cash": portfolio.cash,
            "return_pct": (equity / self.cfg.starting_cash - 1) * 100 if self.cfg.starting_cash else 0.0,
            "position": position,
            "last_checked": self.state.last_checked,
            "ledger": self.state.ledger.to_dict(),
            "equity_curve": self.state.equity_curve[-200:],
            "trades": [
                {
                    "side": t.side, "time": t.time, "price": t.price,
                    "qty": t.qty, "reason": t.reason, "pnl": t.pnl,
                }
                for t in portfolio.trades[-25:]
            ][::-1],
        }


def require_live_confirmation(address: str, var: str = "TRADEBOT_LIVE_CONFIRM") -> None:
    """Live mode needs the wallet address typed back, so it cannot start by accident."""
    supplied = os.environ.get(var, "").strip()
    if supplied != address:
        raise PermissionError(
            f"live mode refused: set {var} to the wallet address you intend to trade "
            f"({address}). This exists so a stray live:true cannot spend the wrong wallet."
        )
