"""Minimal Solana JSON-RPC client (urllib, no dependencies)."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

MAINNET = "https://api.mainnet-beta.solana.com"
DEVNET = "https://api.devnet.solana.com"
LAMPORTS_PER_SOL = 1_000_000_000
USER_AGENT = "trade-bot/0.1"


class RpcError(RuntimeError):
    """A transport failure or an error object returned by the node."""


class RpcClient:
    def __init__(self, url: str = MAINNET, timeout: float = 30.0) -> None:
        self.url = url
        self.timeout = timeout
        self._id = 0

    def call(self, method: str, params: list | None = None):
        self._id += 1
        payload = json.dumps({
            "jsonrpc": "2.0", "id": self._id, "method": method, "params": params or [],
        }).encode()
        request = urllib.request.Request(
            self.url, data=payload,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise RpcError(f"{method}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RpcError(f"{method}: {exc}") from exc
        if "error" in body:
            raise RpcError(f"{method}: {body['error'].get('message', body['error'])}")
        return body.get("result")

    # -- reads -------------------------------------------------------------
    def get_balance(self, address: str) -> int:
        """Native SOL balance in lamports."""
        return int(self.call("getBalance", [address])["value"])

    def get_token_balance(self, owner: str, mint: str) -> float:
        """Summed UI balance of `owner`'s accounts for `mint` (0.0 if none exist)."""
        result = self.call("getTokenAccountsByOwner", [
            owner, {"mint": mint}, {"encoding": "jsonParsed"},
        ])
        total = 0.0
        for account in result.get("value", []):
            info = account["account"]["data"]["parsed"]["info"]["tokenAmount"]
            total += float(info["uiAmount"] or 0.0)
        return total

    def get_latest_blockhash(self) -> tuple[str, int]:
        value = self.call("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]
        return value["blockhash"], int(value["lastValidBlockHeight"])

    def get_block_height(self) -> int:
        return int(self.call("getBlockHeight", [{"commitment": "confirmed"}]))

    # -- writes ------------------------------------------------------------
    def simulate(self, signed_tx_base64: str) -> dict:
        return self.call("simulateTransaction", [
            signed_tx_base64,
            {"encoding": "base64", "commitment": "confirmed", "replaceRecentBlockhash": False},
        ])["value"]

    def send(self, signed_tx_base64: str, skip_preflight: bool = False) -> str:
        return self.call("sendTransaction", [
            signed_tx_base64,
            {"encoding": "base64", "skipPreflight": skip_preflight,
             "preflightCommitment": "confirmed", "maxRetries": 3},
        ])

    def confirm(self, signature: str, timeout: float = 90.0, poll: float = 2.0) -> dict:
        """Block until the signature is confirmed, fails, or the timeout expires."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            statuses = self.call("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])
            status = (statuses.get("value") or [None])[0]
            if status:
                if status.get("err"):
                    raise RpcError(f"transaction {signature} failed on chain: {status['err']}")
                if status.get("confirmationStatus") in ("confirmed", "finalized"):
                    return status
            time.sleep(poll)
        raise RpcError(f"timed out waiting for {signature}; check a block explorer before retrying")
