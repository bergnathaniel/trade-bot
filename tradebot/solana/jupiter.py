"""Jupiter swap aggregator: price quotes and ready-to-sign swap transactions."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

log = logging.getLogger(__name__)

BASE_URL = "https://lite-api.jup.ag/swap/v1"
USER_AGENT = "trade-bot/0.1"

#: Mints the bot is allowed to touch. Anything else is refused before signing.
KNOWN_MINTS = {
    "SOL": ("So11111111111111111111111111111111111111112", 9),
    "USDC": ("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 6),
    "USDT": ("Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", 6),
}


class JupiterError(RuntimeError):
    """A quote or swap-build failure."""


@dataclass(frozen=True)
class Quote:
    input_mint: str
    output_mint: str
    in_amount: int          # base units of the input mint
    out_amount: int         # base units of the output mint
    min_out_amount: int     # what we get in the worst case at this slippage
    price_impact_pct: float  # a fraction, not a percentage: 0.0012 means 0.12%
    raw: dict               # passed back verbatim when building the swap

    @property
    def price(self) -> float:
        return self.out_amount / self.in_amount if self.in_amount else 0.0


def to_base_units(amount: float, decimals: int) -> int:
    return int(round(amount * 10 ** decimals))


def from_base_units(amount: int, decimals: int) -> float:
    return amount / 10 ** decimals


def get_quote(input_mint: str, output_mint: str, amount: int, slippage_bps: int = 50,
              timeout: float = 20.0) -> Quote:
    if amount <= 0:
        raise JupiterError("quote amount must be positive")
    query = urllib.parse.urlencode({
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount,
        "slippageBps": slippage_bps,
        "restrictIntermediateTokens": "true",
    })
    body = _request(f"{BASE_URL}/quote?{query}", timeout=timeout)
    try:
        return Quote(
            input_mint=body["inputMint"],
            output_mint=body["outputMint"],
            in_amount=int(body["inAmount"]),
            out_amount=int(body["outAmount"]),
            min_out_amount=int(body["otherAmountThreshold"]),
            price_impact_pct=float(body.get("priceImpactPct") or 0.0),
            raw=body,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise JupiterError(f"unexpected quote response: {exc}") from None


def build_swap(quote: Quote, user_public_key: str, priority_fee_lamports: int = 100_000,
               timeout: float = 30.0) -> str:
    """Ask Jupiter for the swap transaction. Returns unsigned base64."""
    payload = {
        "quoteResponse": quote.raw,
        "userPublicKey": user_public_key,
        "wrapAndUnwrapSol": True,
        "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": {
            "priorityLevelWithMaxLamports": {
                "maxLamports": priority_fee_lamports,
                "priorityLevel": "medium",
            }
        },
    }
    body = _request(f"{BASE_URL}/swap", data=payload, timeout=timeout)
    transaction = body.get("swapTransaction")
    if not transaction:
        raise JupiterError(f"no swapTransaction in response: {list(body)}")
    return transaction


def _request(url: str, data: dict | None = None, timeout: float = 20.0) -> dict:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    encoded = None
    if data is not None:
        encoded = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=encoded, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:200] if exc.fp else ""
        raise JupiterError(f"HTTP {exc.code} from jupiter: {detail}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise JupiterError(f"jupiter request failed: {exc}") from None
