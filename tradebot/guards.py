"""Hard limits that sit between a strategy signal and a real swap.

Every check here is a refusal, not a warning: if a guard trips, no transaction
is built and nothing is signed. They exist because the strategy is not proven
and the wallet is real.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

HALT_FILENAME = "HALT"


class GuardTripped(RuntimeError):
    """Raised when a limit refuses a trade. The message is safe to log."""


@dataclass
class DailyLedger:
    """Realised PnL and trade count for the current UTC day."""

    day: str = ""
    realised_usd: float = 0.0
    trades: int = 0

    def roll(self, now: float | None = None) -> None:
        today = datetime.fromtimestamp(now or time.time(), timezone.utc).strftime("%Y-%m-%d")
        if today != self.day:
            self.day = today
            self.realised_usd = 0.0
            self.trades = 0

    def record(self, pnl_usd: float) -> None:
        self.roll()
        self.realised_usd += pnl_usd
        self.trades += 1

    def to_dict(self) -> dict:
        return {"day": self.day, "realised_usd": self.realised_usd, "trades": self.trades}

    @classmethod
    def from_dict(cls, data: dict | None) -> "DailyLedger":
        data = data or {}
        return cls(
            day=str(data.get("day", "")),
            realised_usd=float(data.get("realised_usd", 0.0)),
            trades=int(data.get("trades", 0)),
        )


@dataclass
class Limits:
    max_trade_usd: float = 25.0
    daily_loss_limit_usd: float = 50.0
    max_trades_per_day: int = 6
    max_slippage_bps: int = 100
    max_price_impact_pct: float = 1.0
    min_sol_reserve: float = 0.02      # lamports kept back for fees and rent
    allowed_mints: set[str] = field(default_factory=set)


def check_kill_switch(state_dir: str | Path) -> None:
    """`touch state/HALT` stops all live trading without killing the process."""
    halt = Path(state_dir) / HALT_FILENAME
    if halt.exists():
        raise GuardTripped(f"kill switch present ({halt}); remove it to resume live trading")


def check_daily(ledger: DailyLedger, limits: Limits) -> None:
    ledger.roll()
    if ledger.trades >= limits.max_trades_per_day:
        raise GuardTripped(
            f"daily trade cap reached ({ledger.trades}/{limits.max_trades_per_day})"
        )
    if ledger.realised_usd <= -abs(limits.daily_loss_limit_usd):
        raise GuardTripped(
            f"daily loss limit hit ({ledger.realised_usd:.2f} USD); trading halted until UTC midnight"
        )


def check_notional(notional_usd: float, limits: Limits) -> None:
    if notional_usd <= 0:
        raise GuardTripped("computed trade size is zero")
    if notional_usd > limits.max_trade_usd:
        raise GuardTripped(
            f"trade size {notional_usd:.2f} USD exceeds max_trade_usd {limits.max_trade_usd:.2f}"
        )


def check_mints(limits: Limits, *mints: str) -> None:
    if not limits.allowed_mints:
        raise GuardTripped("no allowed_mints configured; refusing to trade")
    for mint in mints:
        if mint not in limits.allowed_mints:
            raise GuardTripped(f"mint {mint} is not on the allowlist")


def check_quote(quote, limits: Limits) -> None:
    """Reject a route that would cost more than we agreed to pay."""
    impact = abs(quote.price_impact_pct) * 100  # jupiter reports a fraction, e.g. 0.0012 = 0.12%
    if impact > limits.max_price_impact_pct:
        raise GuardTripped(
            f"price impact {impact:.2f}% exceeds max_price_impact_pct {limits.max_price_impact_pct:.2f}%"
        )
    if quote.out_amount > 0:
        shortfall_bps = (1 - quote.min_out_amount / quote.out_amount) * 10_000
        if shortfall_bps > limits.max_slippage_bps:
            raise GuardTripped(
                f"route slippage {shortfall_bps:.0f}bps exceeds max_slippage_bps {limits.max_slippage_bps}"
            )


def check_fee_reserve(sol_balance: float, limits: Limits, spending_sol: float = 0.0) -> None:
    remaining = sol_balance - spending_sol
    if remaining < limits.min_sol_reserve:
        raise GuardTripped(
            f"would leave {remaining:.4f} SOL, below min_sol_reserve {limits.min_sol_reserve:.4f}"
        )
