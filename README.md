# trade-bot

A paper-trading bot with a phone-sized dashboard. Pure Python 3.11 standard
library — no pip install, no build step, so it runs anywhere you can get a
shell (including Termux on Android or a $5 VPS you poke at from your phone).

**It does not place real orders.** `live: true` raises `NotImplementedError`
on purpose. What you get is a strategy, an honest backtester, a paper account
that survives restarts, and a way to watch it from your phone.

## Quick start

```bash
git clone https://github.com/bergnathaniel/trade-bot && cd trade-bot

# 1. Score the strategy on generated data (works fully offline)
python3 -m tradebot backtest --synthetic 800

# 2. Score it on real candles
python3 -m tradebot fetch btc.csv --limit 1000
python3 -m tradebot backtest --csv btc.csv --symbol BTCUSDT

# 3. Paper trade with the dashboard
cp config.example.json config.json      # edit it, set a dashboard_token
python3 -m tradebot --config config.json serve --host 127.0.0.1
```

Then open `http://<host>:8000/?token=<your token>` on your phone. Reach it over
Tailscale or an SSH tunnel — the server speaks plain HTTP and has no TLS.

## Commands

| command | what it does |
| --- | --- |
| `backtest` | walks history through the strategy, prints return / drawdown / win rate / Sharpe (`--json` for machine output) |
| `fetch OUT` | downloads candles to a CSV |
| `run` | paper-trades in a loop, logging to stdout |
| `serve` | same loop plus the dashboard on `--port` |
| `show` | prints the effective config with secrets redacted |

## The strategy

Long-only trend following on one symbol:

- **Entry** — fast SMA crosses above slow SMA, and RSI is below `rsi_max_entry`
  (so it does not buy the top of a vertical move).
- **Exit** — an ATR trailing stop that only ever ratchets up, a trend flip
  (fast crosses back below slow), or an optional ATR take-profit.
- **Sizing** — `risk_fraction` of current equity per entry, one position at a
  time, no pyramiding and no leverage.

Fees and slippage are charged on both sides. Signals are read on bar *i* and
filled at the **open of bar i+1**, so the backtest has no lookahead — the
single most common way a strategy looks profitable and isn't.

## About the money

The default parameters lose slowly to buy-and-hold on the built-in synthetic
series, and that is the point of shipping the backtester with it. A trend
follower pays a small toll in chop to catch the occasional large move; over a
few hundred bars you mostly see the toll. Before believing any parameter set:

- test it on several years and several symbols, not one lucky window;
- compare against buy-and-hold, which the summary line prints for you;
- assume anything you tuned to look good in-sample will do worse out-of-sample;
- remember fees and slippage are the difference between most "profitable"
  crossover systems and losing ones.

This is a tool for learning and paper testing, not financial advice, and
nothing here is a plan for reliable income.

## Configuration

Every field in `config.example.json` can be overridden by an environment
variable named `TRADEBOT_<FIELD>` (e.g. `TRADEBOT_SYMBOL=ETHUSDT`,
`TRADEBOT_DASHBOARD_TOKEN=…`). Environment beats file. Put secrets in the
environment, not in a committed config.

Set `telegram_token` and `telegram_chat_id` to get a push on every fill —
that plus the dashboard is the whole phone workflow. A failed notification is
logged and ignored; it never stops the loop.

State (cash, open position, fill history, equity curve) is written atomically
to `state/<symbol>_<interval>.json`, so `serve` can be restarted without losing
the paper account. A corrupt state file is logged and replaced with a fresh
account rather than crashing the bot.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

32 tests cover the indicators, the paper account's fee/slippage arithmetic,
entry and exit conditions, backtest invariants (final equity equals starting
cash plus realised PnL), state persistence and corruption recovery, and the
dashboard's token check.

## Note on the live feed

Candles come from Binance's public REST endpoint, which needs no API key. Some
networks and regions get an HTTP 451 from it — including the sandbox this was
built in. If that happens, fetch a CSV elsewhere and use `source: "csv"`; the
CSV path is exercised by the test suite and by `backtest --csv`.
