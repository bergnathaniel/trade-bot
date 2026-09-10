# trade-bot

A paper-trading bot with a phone-sized dashboard. Pure Python 3.11 standard
library — no pip install, no build step, so it runs anywhere you can get a
shell (including Termux on Android or a $5 VPS you poke at from your phone).

Two modes: **paper** (default, simulated fills) and **live** (real swaps on
Solana through Jupiter, spending a real wallet). Live mode is off by default,
dry-run by default when enabled, and wrapped in hard limits — read
[Live trading](#live-trading-real-money) before you turn it on.

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

## Watching it from an iPhone

iOS has no Termux, so the bot runs on an always-on Linux box (VPS, Raspberry
Pi, spare laptop) and the phone is just the screen:

```bash
sudo bash deploy/setup.sh          # clone to /opt/trade-bot, generate a token,
                                   # install and start the systemd service
```

Install [Tailscale](https://tailscale.com) on that box and on the phone, then
open `http://<tailscale-name>:8000/?token=<token>` in Safari and use
**Share → Add to Home Screen**. The page ships the iOS web-app meta tags, so it
opens full-screen with no browser chrome and the token stays in the saved URL.

Do not port-forward 8000 to the open internet: no TLS, and the token is the
only thing in front of it.

Prefer no dashboard at all? Set `TRADEBOT_TELEGRAM_TOKEN` and
`TRADEBOT_TELEGRAM_CHAT_ID` in `/etc/tradebot.env` and every simulated fill
arrives as a push notification — nothing to check.

Useful once it is running:

```bash
systemctl status tradebot          # is it alive
journalctl -u tradebot -f          # what it is deciding, live
```

## Commands

| command | what it does |
| --- | --- |
| `backtest` | walks history through the strategy, prints return / drawdown / win rate / Sharpe (`--json` for machine output) |
| `fetch OUT` | downloads candles to a CSV |
| `run` | paper-trades in a loop, logging to stdout |
| `serve` | same loop plus the dashboard on `--port` |
| `show` | prints the effective config with secrets redacted |
| `wallet` | prints the burner wallet address and its on-chain balances |
| `quote` | prices a swap through Jupiter, signing nothing |

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

## Live trading (real money)

Live mode swaps SOL against USDC/USDT on Jupiter, signing with a local
ed25519 key. Everything below is deliberate friction.

**Use a burner wallet.** In Phantom, create a *new* account (Settings → Add
Account), send it only what you are willing to lose, and export that account's
key — never your main one. The bot holds the key in full: whatever that wallet
can do, the bot can do.

**The key goes in an environment file, never in the repo and never in CI.**

```bash
sudo install -m 600 /dev/null /etc/tradebot.env
sudo tee -a /etc/tradebot.env >/dev/null <<'ENV'
TRADEBOT_WALLET_KEY=<base58 key Phantom exported>
ENV
```

GitHub Actions is not a place for this: workflow secrets are readable by
anyone who can push a workflow file, and scheduled runs fire late and get
skipped under load. Run it on a box you control.

Then work up in steps, checking after each:

```bash
python3 -m tradebot wallet                       # address + balances; fund it, then re-run
python3 -m tradebot quote --symbol SOLUSDC       # price a swap, signs nothing
python3 -m tradebot --config config.json show    # confirm the limits
```

To go live, set `"symbol": "SOLUSDC"` and `"live": true` in the config, then:

```bash
export TRADEBOT_LIVE_CONFIRM=<the wallet address printed above>
python3 -m tradebot --config config.json serve
```

`dry_run` is still `true` at that point: the bot quotes, builds, signs and
simulates every trade against the chain, then refuses to broadcast and logs
what it would have sent. Watch that for a while. Only then set
`"dry_run": false`.

### What stops a bad trade

Every one of these refuses the trade outright — nothing is signed until all of
them pass:

| guard | default | what it does |
| --- | --- | --- |
| `TRADEBOT_LIVE_CONFIRM` | required | must equal the wallet address, so a stray `live: true` cannot spend the wrong wallet |
| `dry_run` | `true` | signs and simulates, never broadcasts |
| `max_trade_usd` | 25 | caps every single swap |
| `daily_loss_limit_usd` | 50 | halts trading for the UTC day once realised losses hit it |
| `max_trades_per_day` | 6 | caps churn |
| `max_slippage_bps` | 100 | rejects a route whose worst case is worse than 1% |
| `max_price_impact_pct` | 1.0 | rejects a thin route |
| `min_sol_reserve` | 0.02 SOL | never spends the gas money |
| mint allowlist | SOL/USDC/USDT | refuses any other token |
| fee-payer check | always | refuses to sign a transaction whose fee payer is not this wallet |
| simulation | always | a transaction that fails simulation is never broadcast |
| `touch state/HALT` | — | kill switch: stops live trading without stopping the process |

### Kill switch

```bash
touch /opt/trade-bot/state/HALT     # stop trading now; open position is left alone
rm /opt/trade-bot/state/HALT        # resume
```

The kill switch does not sell. To exit a position, swap it back in Phantom.

### What is and is not tested

The full path — quote, build, parse, fee-payer check, sign, verify,
re-serialise — is exercised against live Jupiter responses, and ed25519 is
checked against the RFC 8032 vectors. **Broadcasting is not**: it needs a
funded wallet. Run on devnet (`"rpc_url": "https://api.devnet.solana.com"`) or
with `dry_run: true` and a tiny balance before trusting it with anything.

And the standing caveat: this strategy underperformed buy-and-hold in every
backtest here, on synthetic, BTC and ETH data. Live execution makes the losses
real, not smaller.

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

87 tests cover the indicators, the paper account's fee/slippage arithmetic,
entry and exit conditions, backtest invariants (final equity equals starting
cash plus realised PnL), state persistence and corruption recovery, and the
dashboard's token check, data-source
fallback and symbol mapping, base58 and ed25519 against the RFC 8032 vectors,
transaction parsing and the refusal to sign for another wallet, and every
trading guard (kill switch, caps, slippage, fee reserve, failed simulation).

## Market data

No API keys anywhere. `source: "auto"` (the default) tries `binance`, then
`binance_us`, then `coinbase`, and keeps the first that answers — binance.com
returns HTTP 451 to US addresses, so US networks land on Binance.US or
Coinbase automatically. Pin one with `source`, or set `source: "csv"` with a
`csv_path` to run entirely offline.

Symbols are written Binance-style (`BTCUSDT`, `ETHUSDT`) and mapped to
Coinbase product ids as needed (`BTC-USD`); USDT and USDC both map to USD.
Coinbase supports 1m/5m/15m/1h/6h/1d intervals and about 300 candles per
request.
