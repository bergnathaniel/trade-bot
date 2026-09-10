"""Execution back ends: the paper account, and real Solana swaps via Jupiter.

The engine talks to an Executor and does not know which one it has. Live mode
adds three things the paper path does not have: on-chain balances, guardrails,
and a transaction it has to sign.
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

from . import guards
from .config import Config
from .guards import DailyLedger, GuardTripped, Limits
from .portfolio import Portfolio, Position, Trade
from .solana import jupiter
from .solana.keypair import Keypair
from .solana.rpc import LAMPORTS_PER_SOL, RpcClient, RpcError
from .solana.transaction import Transaction

log = logging.getLogger(__name__)


class Executor(Protocol):
    portfolio: Portfolio

    def refresh(self, price: float) -> None: ...
    def buy(self, price: float, time_ms: int, reason: str) -> Trade | None: ...
    def sell(self, price: float, time_ms: int, reason: str) -> Trade | None: ...
    def equity(self, price: float) -> float: ...


class PaperExecutor:
    """Simulated fills. This is what every mode except `live: true` uses."""

    mode = "paper"

    def __init__(self, cfg: Config, portfolio: Portfolio) -> None:
        self.cfg = cfg
        self.portfolio = portfolio

    def refresh(self, price: float) -> None:
        return None

    def buy(self, price: float, time_ms: int, reason: str) -> Trade | None:
        qty = self.portfolio.size_for(price, self.cfg.risk_fraction)
        return self.portfolio.buy(price, qty, time_ms, reason)

    def sell(self, price: float, time_ms: int, reason: str) -> Trade | None:
        return self.portfolio.sell(price, time_ms, reason)

    def equity(self, price: float) -> float:
        return self.portfolio.equity(price)


class LiveExecutor:
    """Real swaps on Solana mainnet, spending a real wallet's balance.

    Ordering is deliberate on every trade: guards, then quote, then guards on
    the quote, then build, then a fee-payer check, then sign, then simulate,
    then send. Nothing is signed until every limit has passed.
    """

    mode = "live"

    def __init__(self, cfg: Config, portfolio: Portfolio, keypair: Keypair,
                 rpc: RpcClient | None = None, ledger: DailyLedger | None = None) -> None:
        self.cfg = cfg
        self.portfolio = portfolio
        self.keypair = keypair
        self.rpc = rpc or RpcClient(cfg.rpc_url)
        self.ledger = ledger or DailyLedger()
        self.base_symbol, self.quote_symbol = split_pair(cfg.symbol)
        self.base_mint, self.base_decimals = jupiter.KNOWN_MINTS[self.base_symbol]
        self.quote_mint, self.quote_decimals = jupiter.KNOWN_MINTS[self.quote_symbol]
        self.limits = Limits(
            max_trade_usd=cfg.max_trade_usd,
            daily_loss_limit_usd=cfg.daily_loss_limit_usd,
            max_trades_per_day=cfg.max_trades_per_day,
            max_slippage_bps=cfg.max_slippage_bps,
            max_price_impact_pct=cfg.max_price_impact_pct,
            min_sol_reserve=cfg.min_sol_reserve,
            allowed_mints={self.base_mint, self.quote_mint},
        )
        self.base_balance = 0.0
        self.quote_balance = 0.0
        self.sol_balance = 0.0

    # -- balances ----------------------------------------------------------
    def refresh(self, price: float) -> None:
        address = self.keypair.address
        self.sol_balance = self.rpc.get_balance(address) / LAMPORTS_PER_SOL
        self.base_balance = (
            self.sol_balance if self.base_symbol == "SOL"
            else self.rpc.get_token_balance(address, self.base_mint)
        )
        self.quote_balance = (
            self.sol_balance if self.quote_symbol == "SOL"
            else self.rpc.get_token_balance(address, self.quote_mint)
        )

    def equity(self, price: float) -> float:
        return self.quote_balance + self.base_balance * price

    # -- trades ------------------------------------------------------------
    def buy(self, price: float, time_ms: int, reason: str) -> Trade | None:
        self.refresh(price)
        notional = min(self.quote_balance * self.cfg.risk_fraction, self.cfg.max_trade_usd)
        try:
            self._preflight(notional)
            amount = jupiter.to_base_units(notional, self.quote_decimals)
            filled_base, filled_quote = self._swap(self.quote_mint, self.base_mint, amount,
                                                  self.base_decimals, self.quote_decimals)
        except (GuardTripped, jupiter.JupiterError, RpcError, ValueError) as exc:
            log.warning("buy refused: %s", exc)
            return None

        fill_price = filled_quote / filled_base if filled_base else price
        self.portfolio.cash = max(0.0, self.portfolio.cash - filled_quote)
        self.portfolio.position = Position(qty=filled_base, entry_price=fill_price,
                                           entry_time=time_ms, peak=fill_price)
        trade = Trade("buy", time_ms, fill_price, filled_base, 0.0, reason)
        self.portfolio.trades.append(trade)
        self.ledger.record(0.0)
        return trade

    def sell(self, price: float, time_ms: int, reason: str) -> Trade | None:
        position = self.portfolio.position
        if position is None:
            return None
        self.refresh(price)
        qty = min(position.qty, self.base_balance)
        try:
            self._preflight(qty * price)
            amount = jupiter.to_base_units(qty, self.base_decimals)
            filled_quote, filled_base = self._swap(self.base_mint, self.quote_mint, amount,
                                                   self.quote_decimals, self.base_decimals)
        except (GuardTripped, jupiter.JupiterError, RpcError, ValueError) as exc:
            log.warning("sell refused: %s", exc)
            return None

        fill_price = filled_quote / filled_base if filled_base else price
        pnl = (fill_price - position.entry_price) * filled_base
        self.portfolio.cash += filled_quote
        self.portfolio.position = None
        trade = Trade("sell", time_ms, fill_price, filled_base, 0.0, reason, pnl)
        self.portfolio.trades.append(trade)
        self.ledger.record(pnl)
        return trade

    # -- the dangerous part ------------------------------------------------
    def _preflight(self, notional_usd: float) -> None:
        guards.check_kill_switch(self.cfg.state_dir)
        guards.check_daily(self.ledger, self.limits)
        guards.check_notional(notional_usd, self.limits)
        guards.check_mints(self.limits, self.base_mint, self.quote_mint)
        guards.check_fee_reserve(self.sol_balance, self.limits)

    def _swap(self, input_mint: str, output_mint: str, amount: int,
              out_decimals: int, in_decimals: int) -> tuple[float, float]:
        """Quote, guard, sign, simulate, send. Returns (out_amount, in_amount) as UI floats."""
        quote = jupiter.get_quote(input_mint, output_mint, amount, self.cfg.max_slippage_bps)
        guards.check_quote(quote, self.limits)

        unsigned = jupiter.build_swap(quote, self.keypair.address, self.cfg.priority_fee_lamports)
        transaction = Transaction.from_base64(unsigned)
        signature = transaction.sign_as_fee_payer(self.keypair)  # refuses if we are not the fee payer
        signed = transaction.to_base64()

        simulation = self.rpc.simulate(signed)
        if simulation.get("err"):
            raise RpcError(f"simulation failed: {simulation['err']}")

        if self.cfg.dry_run:
            log.warning("DRY RUN: not broadcasting %s (%s -> %s, %s base units)",
                        signature, input_mint[:6], output_mint[:6], amount)
            raise GuardTripped("dry_run is on; set dry_run=false to broadcast")

        sent = self.rpc.send(signed)
        log.info("sent %s https://solscan.io/tx/%s", sent, sent)
        self.rpc.confirm(sent, timeout=self.cfg.confirm_timeout)
        time.sleep(2)  # let balances settle before the next read
        return (jupiter.from_base_units(quote.out_amount, out_decimals),
                jupiter.from_base_units(quote.in_amount, in_decimals))


def split_pair(symbol: str) -> tuple[str, str]:
    """SOLUSDC / SOL-USDC / SOLUSDT -> ('SOL', 'USDC'). Both sides must be known mints."""
    upper = symbol.upper().replace("-", "")
    for quote in ("USDC", "USDT", "SOL"):
        if upper.endswith(quote) and len(upper) > len(quote):
            base = upper[: -len(quote)]
            if base in jupiter.KNOWN_MINTS and quote in jupiter.KNOWN_MINTS:
                return base, quote
    raise ValueError(
        f"live trading needs a pair of known mints, got {symbol!r}; "
        f"known: {sorted(jupiter.KNOWN_MINTS)}"
    )
