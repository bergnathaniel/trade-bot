"""Command line: `python -m tradebot <backtest|run|serve|fetch|show>`."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import backtest, server
from .config import Config
from .data import DataError, load_candles, read_csv, synthetic, write_csv
from .engine import Engine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tradebot", description=__doc__)
    parser.add_argument("--config", help="path to a JSON config file")
    parser.add_argument("--symbol")
    parser.add_argument("--interval")
    parser.add_argument("--verbose", "-v", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    bt = sub.add_parser("backtest", help="score the strategy over historical candles")
    bt.add_argument("--csv", help="candles CSV instead of the live feed")
    bt.add_argument("--synthetic", type=int, metavar="N", help="use N generated candles (offline demo)")
    bt.add_argument("--json", action="store_true", help="emit machine-readable output")

    sub.add_parser("run", help="paper-trade in a loop, printing to stdout")

    srv = sub.add_parser("serve", help="paper-trade and serve the phone dashboard")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int)

    fetch = sub.add_parser("fetch", help="download candles to a CSV")
    fetch.add_argument("out")
    fetch.add_argument("--limit", type=int, default=1000)

    sub.add_parser("show", help="print the effective configuration")
    return parser


def apply_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    if args.symbol:
        cfg.symbol = args.symbol
    if args.interval:
        cfg.interval = args.interval
    if getattr(args, "port", None):
        cfg.dashboard_port = args.port
    return cfg


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )
    cfg = apply_overrides(Config.load(args.config), args)

    if args.command == "show":
        print(cfg.to_json())
        return 0

    if args.command == "backtest":
        if args.synthetic:
            candles = synthetic(args.synthetic)
        elif args.csv:
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
    except DataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(entrypoint())
