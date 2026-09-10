"""A phone-sized dashboard, served from the standard library.

Bind to localhost and reach it over an SSH/Tailscale tunnel, or set
dashboard_token so the page needs `?token=...`. There is no TLS here.
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .config import Config
from .engine import Engine

log = logging.getLogger(__name__)

PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>trade-bot</title>
<style>
 :root{color-scheme:light dark;--bg:#0f1115;--card:#181b22;--fg:#e8eaed;--dim:#9aa0aa;--up:#35c07d;--down:#e2604f}
 @media (prefers-color-scheme:light){:root{--bg:#f4f5f7;--card:#fff;--fg:#15171c;--dim:#666e7a}}
 *{box-sizing:border-box}
 body{margin:0;padding:16px;background:var(--bg);color:var(--fg);
      font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
 h1{font-size:17px;margin:0 0 12px;display:flex;justify-content:space-between;align-items:baseline}
 h1 small{color:var(--dim);font-weight:400;font-size:12px}
 .card{background:var(--card);border-radius:14px;padding:14px;margin-bottom:12px}
 .big{font-size:30px;font-weight:600;letter-spacing:-.5px}
 .row{display:flex;justify-content:space-between;padding:5px 0;font-variant-numeric:tabular-nums}
 .row span:first-child{color:var(--dim)}
 .up{color:var(--up)}.down{color:var(--down)}
 table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
 td{padding:6px 0;border-top:1px solid rgba(128,128,128,.2)}
 td:last-child{text-align:right}
 .dim{color:var(--dim);font-size:12px}
</style>
<h1>trade-bot <small id="mode">paper</small></h1>
<div class="card">
  <div class="dim" id="sym">—</div>
  <div class="big" id="equity">—</div>
  <div id="ret" class="dim">—</div>
</div>
<div class="card" id="stats"></div>
<div class="card"><div class="dim" style="margin-bottom:6px">recent fills</div>
  <table id="trades"></table></div>
<div class="dim" id="foot">loading…</div>
<script>
const q = new URLSearchParams(location.search).get('token');
const money = n => (n ?? 0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
const cls = n => n > 0 ? 'up' : n < 0 ? 'down' : '';
async function tick(){
  try{
    const r = await fetch('/api/state' + (q ? '?token=' + encodeURIComponent(q) : ''));
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const s = await r.json();
    document.getElementById('mode').textContent = s.mode;
    document.getElementById('sym').textContent = s.symbol + ' · ' + s.interval;
    document.getElementById('equity').textContent = money(s.equity);
    const ret = document.getElementById('ret');
    ret.textContent = (s.return_pct >= 0 ? '+' : '') + s.return_pct.toFixed(2) + '% since start';
    ret.className = cls(s.return_pct);
    const rows = [['price', money(s.price)], ['cash', money(s.cash)]];
    if(s.position){
      rows.push(['position', s.position.qty.toFixed(6)]);
      rows.push(['entry', money(s.position.entry_price)]);
      rows.push(['stop', money(s.position.stop)]);
      rows.push(['unrealised', money(s.position.unrealised)]);
    } else { rows.push(['position', 'flat']); }
    document.getElementById('stats').innerHTML = rows
      .map(([k, v]) => `<div class="row"><span>${k}</span><span>${v}</span></div>`).join('');
    document.getElementById('trades').innerHTML = s.trades.length ? s.trades.map(t => `<tr>
        <td>${t.side.toUpperCase()}</td>
        <td class="dim">${new Date(t.time).toLocaleString()}</td>
        <td class="${t.side === 'sell' ? cls(t.pnl) : ''}">${money(t.price)}${
          t.side === 'sell' ? ' · ' + (t.pnl >= 0 ? '+' : '') + money(t.pnl) : ''}</td>
      </tr>`).join('') : '<tr><td class="dim">no fills yet</td></tr>';
    document.getElementById('foot').textContent = 'checked ' +
      (s.last_checked ? new Date(s.last_checked * 1000).toLocaleTimeString() : 'never');
  }catch(e){ document.getElementById('foot').textContent = 'error: ' + e.message; }
}
tick(); setInterval(tick, 15000);
</script>
"""


def make_handler(engine: Engine, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "tradebot"

        def _authorised(self, query: dict) -> bool:
            if not token:
                return True
            supplied = (query.get("token") or [""])[0]
            return hmac.compare_digest(supplied, token)

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/healthz":
                return self._send(200, b"ok", "text/plain; charset=utf-8")
            if not self._authorised(query):
                return self._send(403, b"forbidden", "text/plain; charset=utf-8")
            if parsed.path in ("/", "/index.html"):
                return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            if parsed.path == "/api/state":
                body = json.dumps(engine.snapshot()).encode()
                return self._send(200, body, "application/json")
            self._send(404, b"not found", "text/plain; charset=utf-8")

        def log_message(self, fmt: str, *args) -> None:
            log.debug("%s - %s", self.address_string(), fmt % args)

    return Handler


def serve(cfg: Config, host: str = "127.0.0.1", background_loop: bool = True) -> None:
    engine = Engine(cfg)
    if background_loop:
        threading.Thread(target=_loop, args=(engine,), daemon=True).start()
    httpd = ThreadingHTTPServer((host, cfg.dashboard_port), make_handler(engine, cfg.dashboard_token))
    suffix = f"?token={cfg.dashboard_token}" if cfg.dashboard_token else ""
    log.info("dashboard on http://%s:%s/%s", host, cfg.dashboard_port, suffix)
    if not cfg.dashboard_token and host not in ("127.0.0.1", "localhost"):
        log.warning("no dashboard_token set and bound to %s - anyone on the network can read this", host)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        httpd.server_close()


def _loop(engine: Engine) -> None:
    while True:
        try:
            engine.step()
        except Exception:
            log.exception("step failed")
        time.sleep(max(5, engine.cfg.poll_seconds))
