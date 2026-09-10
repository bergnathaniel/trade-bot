"""Command line: `python -m tradebot <backtest|run|serve|fetch|show|wallet|quote>`."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import backtest, server
from .config import Config
from .data import DataError, load_candles, read_csv, synthetic, write_csv
from .engine import Engine
from .solana.jupiter import JupiterError, KNOWN_MINTS
from .solana.keypair import KeyError_
from .solana.rpc import RpcError


def build_parser() -> argparse.ArgumentParser:
    # Shared options are accepted on either side of the subcommand, so both
    # `tradebot --symbol X backtest` and `tradebot backtest --symbol X` work.
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--config", help="path to a JSON config file")
    common.add_argument("--symbol", help="e.g. BTCUSDT")
    common.add_argument("--interval", help="e.g. 1h")
    common.add_argument("--source", help="auto | binance | binance_us | coinbase | csv")
    common.add_argument("--verbose", "-v", action="store_true")

    parser = argparse.ArgumentParser(prog="tradebot", description=__doc__, parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)

    bt = sub.add_parser("backtest", parents=[common],
                        help="score the strategy over historical candles")
    bt.add_argument("--csv", help="candles CSV instead of the live feed")
    bt.add_argument("--synthetic", type=int, metavar="N", help="use N generated candles (offline demo)")
    bt.add_argument("--json", action="store_true", help="emit machine-readable output")

    sub.add_parser("run", parents=[common], help="paper-trade in a loop, printing to stdout")

    srv = sub.add_parser("serve", parents=[common],
                         help="paper-trade and serve the phone dashboard")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int)

    fetch = sub.add_parser("fetch", parents=[common], help="download candles to a CSV")
    fetch.add_argument("out")
    fetch.add_argument("--limit", type=int, default=1000)

    sub.add_parser("show", parents=[common], help="print the effective configuration")

    sub.add_parser("wallet", parents=[common],
                   help="print the burner wallet address and its on-chain balances")

    quote = sub.add_parser("quote", parents=[common],
                           help="price a swap through Jupiter without signing anything")
    quote.add_argument("--usd", type=float, default=25.0, help="notional to price, in quote units")
    return parser


def apply_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    for name, field in (("symbol", "symbol"), ("interval", "interval"),
                        ("source", "source"), ("port", "dashboard_port")):
        value = getattr(args, name, None)
        if value:
            setattr(cfg, field, value)
    return cfg


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )
    cfg = apply_overrides(Config.load(getattr(args, "config", None)), args)

    if args.command == "show":
        print(cfg.to_json())
        return 0

    if args.command == "backtest":
        if getattr(args, "synthetic", None):
            candles = synthetic(args.synthetic)
        elif getattr(args, "csv", None):
            candles = read_csv(args.csv)
        else:
            candles = load_candles(cfg.source, cfg.symbol, cfg.interval, cfg.lookback, cfg.csv_path)
        result = backtest.run(candles, cfg)
        if args.json:
            print(json.dumps({
                "candles": len(candles),
                "start_equity": result.start_equity,
                "end_equity": result.end_equity,
                "total_return": result.total_return,
                "buy_and_hold": result.buy_and_hold,
                "max_drawdown": result.max_drawdown,
                "win_rate": result.win_rate,
                "sharpe_per_bar": result.sharpe,
                "trades": len(result.closed_trades),
            }, indent=2))
        else:
            print(f"{cfg.symbol} {cfg.interval}  {len(candles)} candles")
            print(result.summary())
            for trade in result.closed_trades[-10:]:
                print(f"  sell @ {trade.price:>12.2f}  pnl {trade.pnl:+9.2f}  ({trade.reason})")
        return 0

    if args.command == "fetch":
        candles = load_candles(cfg.source, cfg.symbol, cfg.interval, args.limit, cfg.csv_path)
        write_csv(args.out, candles)
        print(f"wrote {len(candles)} candles to {args.out}")
        return 0

    if args.command == "wallet":
        from .solana.keypair import Keypair
        from .solana.rpc import LAMPORTS_PER_SOL, RpcClient

        keypair = Keypair.from_env(cfg.wallet_key_env)
        client = RpcClient(cfg.rpc_url)
        print(f"address: {keypair.address}")
        print(f"explorer: https://solscan.io/account/{keypair.address}")
        sol = client.get_balance(keypair.address) / LAMPORTS_PER_SOL
        print(f"SOL: {sol:.6f}")
        for name, (mint, _) in KNOWN_MINTS.items():
            if name == "SOL":
                continue
            balance = client.get_token_balance(keypair.address, mint)
            if balance:
                print(f"{name}: {balance:.6f}")
        print(f"\nto trade live, set TRADEBOT_LIVE_CONFIRM={keypair.address}")
        return 0

    if args.command == "quote":
        from .execution import split_pair
        from .solana import jupiter

        base, quote_symbol = split_pair(cfg.symbol)
        base_mint, base_decimals = KNOWN_MINTS[base]
        quote_mint, quote_decimals = KNOWN_MINTS[quote_symbol]
        amount = jupiter.to_base_units(args.usd, quote_decimals)
        result = jupiter.get_quote(quote_mint, base_mint, amount, cfg.max_slippage_bps)
        received = jupiter.from_base_units(result.out_amount, base_decimals)
        worst = jupiter.from_base_units(result.min_out_amount, base_decimals)
        print(f"{args.usd:g} {quote_symbol} -> {received:.6f} {base} "
              f"(worst case {worst:.6f}, price impact {result.price_impact_pct}%)")
        print(f"implied price: {args.usd / received:.4f} {quote_symbol}/{base}" if received else "")
        return 0

    if args.command == "run":
        Engine(cfg).run_forever()
        return 0

    if args.command == "serve":
        server.serve(cfg, host=args.host)
        return 0

    return 1


def entrypoint() -> int:
    try:
        return main()
    except (DataError, JupiterError, RpcError, KeyError_, PermissionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(entrypoint())
