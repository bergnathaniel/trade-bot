"""Paper-trading account: cash, one long position at a time, fees and slippage."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Position:
    qty: float
    entry_price: float
    entry_time: int
    stop: float = 0.0
    peak: float = 0.0  # highest close seen while the position was open


@dataclass
class Trade:
    side: str            # "buy" or "sell"
    time: int
    price: float         # fill price, slippage included
    qty: float
    fee: float
    reason: str = ""
    pnl: float = 0.0     # realised, net of fees, set on the closing fill


@dataclass
class Portfolio:
    cash: float
    fee_rate: float = 0.001
    slippage_rate: float = 0.0005
    position: Position | None = None
    trades: list[Trade] = field(default_factory=list)

    # -- helpers ---------------------------------------------------------
    def equity(self, price: float) -> float:
        if self.position is None:
            return self.cash
        return self.cash + self.position.qty * price

    @property
    def is_long(self) -> bool:
        return self.position is not None

    def size_for(self, price: float, risk_fraction: float) -> float:
        """Quantity affordable when committing `risk_fraction` of current equity."""
        budget = self.equity(price) * risk_fraction
        budget = min(budget, self.cash)
        fill = price * (1 + self.slippage_rate)
        if fill <= 0:
            return 0.0
        return budget / (fill * (1 + self.fee_rate))

    # -- fills -----------------------------------------------------------
    def buy(self, price: float, qty: float, time: int, reason: str = "") -> Trade | None:
        if qty <= 0 or self.position is not None:
            return None
        fill = price * (1 + self.slippage_rate)
        cost = fill * qty
        fee = cost * self.fee_rate
        if cost + fee > self.cash + 1e-9:
            return None
        self.cash -= cost + fee
        self.position = Position(qty=qty, entry_price=fill, entry_time=time, peak=fill)
        trade = Trade("buy", time, fill, qty, fee, reason)
        self.trades.append(trade)
        return trade

    def sell(self, price: float, time: int, reason: str = "") -> Trade | None:
        if self.position is None:
            return None
        pos = self.position
        fill = price * (1 - self.slippage_rate)
        proceeds = fill * pos.qty
        fee = proceeds * self.fee_rate
        self.cash += proceeds - fee
        entry_fee = next(
            (t.fee for t in reversed(self.trades) if t.side == "buy" and t.time == pos.entry_time),
            0.0,
        )
        pnl = (fill - pos.entry_price) * pos.qty - fee - entry_fee
        self.position = None
        trade = Trade("sell", time, fill, pos.qty, fee, reason, pnl)
        self.trades.append(trade)
        return trade

    # -- persistence -----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "cash": self.cash,
            "fee_rate": self.fee_rate,
            "slippage_rate": self.slippage_rate,
            "position": asdict(self.position) if self.position else None,
            "trades": [asdict(t) for t in self.trades],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Portfolio":
        pos = data.get("position")
        return cls(
            cash=float(data["cash"]),
            fee_rate=float(data.get("fee_rate", 0.001)),
            slippage_rate=float(data.get("slippage_rate", 0.0005)),
            position=Position(**pos) if pos else None,
            trades=[Trade(**t) for t in data.get("trades", [])],
        )
