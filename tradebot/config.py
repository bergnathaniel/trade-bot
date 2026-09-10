"""Configuration, loaded from a JSON file and/or environment variables."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DEFAULT_STATE_DIR = Path(os.environ.get("TRADEBOT_STATE_DIR", "./state"))


@dataclass
class Config:
    # market
    symbol: str = "BTCUSDT"
    interval: str = "1h"
    source: str = "auto"             # auto | binance | binance_us | coinbase | csv
    csv_path: str = ""               # used when source == "csv"

    # strategy
    fast_period: int = 20
    slow_period: int = 50
    rsi_period: int = 14
    rsi_max_entry: float = 70.0      # skip entries into overbought moves
    atr_period: int = 14
    atr_stop_mult: float = 2.5       # trailing stop distance, in ATRs
    take_profit_mult: float = 0.0    # 0 disables the fixed take-profit

    # money
    starting_cash: float = 1000.0
    risk_fraction: float = 0.25      # share of equity committed per entry
    fee_rate: float = 0.001          # 0.1% per fill, both sides
    slippage_rate: float = 0.0005

    # execution
    live: bool = False               # True = real swaps with real funds, read the README first
    dry_run: bool = True             # live mode still stops short of broadcasting
    poll_seconds: int = 60
    lookback: int = 500

    # live trading (Solana / Jupiter). Ignored unless live is true.
    rpc_url: str = "https://api.mainnet-beta.solana.com"
    wallet_key_env: str = "TRADEBOT_WALLET_KEY"
    max_trade_usd: float = 25.0
    daily_loss_limit_usd: float = 50.0
    max_trades_per_day: int = 6
    max_slippage_bps: int = 100
    max_price_impact_pct: float = 1.0
    min_sol_reserve: float = 0.02
    priority_fee_lamports: int = 100_000
    confirm_timeout: float = 90.0

    # ops
    state_dir: str = str(DEFAULT_STATE_DIR)
    telegram_token: str = ""
    telegram_chat_id: str = ""
    dashboard_token: str = ""        # required query token for the web dashboard
    dashboard_port: int = 8000
    tags: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> "Config":
        data: dict = {}
        if path:
            data = json.loads(Path(path).read_text())
        cfg = cls(**{k: v for k, v in data.items() if k in {f.name for f in fields(cls)}})
        return cfg.with_env()

    def with_env(self) -> "Config":
        """Environment variables (TRADEBOT_<FIELD>) win over the file."""
        out = asdict(self)
        for f in fields(self):
            raw = os.environ.get(f"TRADEBOT_{f.name.upper()}")
            if raw is None:
                continue
            out[f.name] = _coerce(raw, out[f.name])
        return Config(**out)

    @property
    def state_path(self) -> Path:
        return Path(self.state_dir) / f"{self.symbol}_{self.interval}.json"

    def to_json(self) -> str:
        redacted = asdict(self)
        for secret in ("telegram_token", "dashboard_token"):
            if redacted[secret]:
                redacted[secret] = "***"
        return json.dumps(redacted, indent=2, sort_keys=True)


def _coerce(raw: str, current):
    if isinstance(current, bool):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, list):
        return [part for part in (p.strip() for p in raw.split(",")) if part]
    return raw
