import os
import time
import threading

import requests
from flask import Flask, jsonify, render_template

app = Flask(__name__)

# Paper-only scanner. No wallet, signing or real transactions.
QUOTE = "https://lite-api.jup.ag/swap/v1/quote"
SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
DEXES = ["Raydium", "Orca V2", "Meteora DLMM", "Whirlpool"]


def num(key, default):
    try:
        return float(os.getenv(key, default))
    except (TypeError, ValueError):
        return float(default)


POLL = max(60, int(num("POLL_SECONDS", "90")))
START = max(0.01, num("STARTING_USDC", "1"))
FRACTION = min(1.0, max(0.01, num("TRADE_FRACTION", ".10")))
MINP = max(0.0, num("MIN_NET_PROFIT_PCT", ".15"))
FEE = max(0.0, num("PAPER_FEE_PCT", ".20"))
SLIP = max(0.0, num("PAPER_SLIPPAGE_PCT", ".15"))
PROBE = max(0.01, num("PROBE_USDC", "1"))
TOP_BUYS = min(2, max(1, int(num("TOP_BUYS_TO_VALIDATE", "2"))))
REQUEST_GAP = max(0.5, num("REQUEST_GAP_SECONDS", "1.0"))
RATE_LIMIT_FALLBACK = max(30, int(num("RATE_LIMIT_COOLDOWN_SECONDS", "90")))

S = {
    "running": True,
    "balance": START,
    "starting_balance": START,
    "cycles": 0,
    "opportunities": 0,
    "paper_trades": 0,
    "last_scan": None,
    "best": None,
    "quotes": [],
    "logs": [],
    "scan_in_progress": False,
    "last_error": None,
    "last_scan_ok": False,
    "requests_last_scan": 0,
    "rate_limited_until": 0,
}


def log(message):
    S["logs"].insert(0, time.strftime("%H:%M:%S") + "  " + message)
    del S["logs"][50:]


_request_lock = threading.Lock()
_last_request_at = 0.0
_session = requests.Session()
_session.headers.update({"Accept": "application/json", "User-Agent": "Solana-Arbitrage-Paper-Bot/7.0"})

def rate_limit_remaining():
    return max(0, int(S["rate_limited_until"] - time.time()))

def set_rate_limit(seconds, retry_after=None):
    seconds = max(30, min(300, int(seconds)))
    S["rate_limited_until"] = max(S["rate_limited_until"], time.time() + seconds)
    log(f"Jupiter rate limit: cooling down for {seconds}s" + (f" (Retry-After={retry_after})" if retry_after else ""))


def quote(input_mint, output_mint, amount_raw, dex):
    remaining = rate_limit_remaining()
    if remaining > 0:
        raise RuntimeError(f"Jupiter cooldown active ({remaining}s)")

    global _last_request_at
    with _request_lock:
        wait = REQUEST_GAP - (time.time() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        params = {
            "inputMint": input_mint, "outputMint": output_mint,
            "amount": str(int(amount_raw)), "slippageBps": "50",
            "swapMode": "ExactIn", "dexes": dex, "onlyDirectRoutes": "true",
        }
        _last_request_at = time.time()
        response = _session.get(QUOTE, params=params, timeout=(5, 12))

    if response.status_code == 429:
        retry_after_raw = response.headers.get("Retry-After")
        try:
            retry_after = int(float(retry_after_raw))
        except (TypeError, ValueError):
            retry_after = RATE_LIMIT_FALLBACK
        set_rate_limit(retry_after, retry_after_raw)
        raise RuntimeError("Jupiter rate limited (429)")
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        raise RuntimeError(str(data.get("error")))
    return data


def buy_quote(dex, amount_usdc):
    try:
        data = quote(USDC, SOL, amount_usdc * 1_000_000, dex)
        raw = int(data.get("outAmount", "0"))
        if raw <= 0:
            return None, f"{dex}: empty buy quote"
        return {
            "dex": dex,
            "sol_raw": raw,
            "sol": raw / 1e9,
            "impact": float(data.get("priceImpactPct") or 0),
        }, None
    except Exception as exc:
        return None, f"{dex}: buy quote unavailable ({str(exc)[:80]})"


def sell_quote(buy, dex):
    try:
        data = quote(SOL, USDC, buy["sol_raw"], dex)
        raw = int(data.get("outAmount", "0"))
        if raw <= 0:
            return None
        return {"buy": buy["dex"], "sell": dex, "usdc": raw / 1e6}
    except Exception:
        return None


def scan():
    started = time.time()
    requests_used = 0
    amount = min(PROBE, max(0.01, S["balance"] * FRACTION))

    buys = []
    for dex in DEXES:
        result, error = buy_quote(dex, amount)
        requests_used += 1
        if result:
            buys.append(result)
        elif error:
            log(error)
        if rate_limit_remaining() > 0:
            break

    S["requests_last_scan"] = requests_used
    if not buys:
        raise RuntimeError("No fresh buy quotes returned")

    buys.sort(key=lambda item: item["sol"], reverse=True)
    S["quotes"] = buys

    sells = []
    for buy in buys[:TOP_BUYS]:
        for dex in DEXES:
            if dex == buy["dex"]:
                continue
            result = sell_quote(buy, dex)
            requests_used += 1
            if result:
                sells.append(result)
            if rate_limit_remaining() > 0:
                break
        if rate_limit_remaining() > 0:
            break

    S["requests_last_scan"] = requests_used
    if not sells:
        raise RuntimeError("No fresh cross-DEX sell quotes returned")

    best = max(sells, key=lambda item: item["usdc"])
    gross_pct = (best["usdc"] / amount - 1) * 100
    netout = best["usdc"] * (1 - FEE / 100) * (1 - SLIP / 100)
    net = netout - amount
    net_pct = (netout / amount - 1) * 100
    now = time.strftime("%H:%M:%S")
    S["last_scan"] = now
    S["last_scan_ok"] = True
    S["last_error"] = None
    S["best"] = {"input": amount, "buy": best["buy"], "sell": best["sell"],
                 "gross": best["usdc"], "gross_pct": gross_pct, "netout": netout,
                 "net": net, "net_pct": net_pct, "time": now, "requests": requests_used,
                 "duration": round(time.time() - started, 2)}
    if net_pct >= MINP:
        S["opportunities"] += 1
        profit = net * (min(amount, S["balance"] * FRACTION) / amount)
        S["balance"] += profit
        S["paper_trades"] += 1
        log(f"PAPER TRADE {best['buy']} -> {best['sell']} | +${profit:.8f} ({net_pct:.4f}%)")
    else:
        log(f"No trade: best net {net_pct:.4f}% < {MINP:.4f}% ({requests_used} requests)")


def worker():
    log("Scanner worker started")
    while True:
        if S["running"] and not S["scan_in_progress"]:
            if rate_limit_remaining() > 0:
                time.sleep(min(rate_limit_remaining(), 15))
                continue
            S["scan_in_progress"] = True
            S["cycles"] += 1
            log(f"Scan #{S['cycles']} started")
            try:
                scan()
            except Exception as exc:
                S["last_error"] = str(exc)[:160]
                log("Scan error: " + str(exc)[:160])
            finally:
                S["scan_in_progress"] = False
        time.sleep(POLL)


_scanner_started = False
_scanner_lock = threading.Lock()


def ensure_scanner_started():
    global _scanner_started
    if _scanner_started:
        return
    with _scanner_lock:
        if _scanner_started:
            return
        threading.Thread(
            target=worker,
            name="paper-scanner",
            daemon=True,
        ).start()
        _scanner_started = True
        log("Scanner launched inside web worker")


@app.before_request
def start_scanner_if_needed():
    # Important for Gunicorn/Render: this starts the thread after the worker
    # process exists, instead of starting it during the Gunicorn master import.
    ensure_scanner_started()


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/status")
def status():
    return jsonify(
        {
            **S,
            "profit": S["balance"] - S["starting_balance"],
            "rate_limit_remaining": rate_limit_remaining(),
            "settings": {
                "poll": POLL,
                "fraction": FRACTION,
                "min_profit_pct": MINP,
                "fee_pct": FEE,
                "slippage_pct": SLIP,
                "probe_usdc": PROBE,
                "top_buys_to_validate": TOP_BUYS,
                "request_gap_seconds": REQUEST_GAP,
                "dexes": DEXES,
            },
        }
    )


@app.post("/api/toggle")
def toggle():
    S["running"] = not S["running"]
    log("Bot " + ("started" if S["running"] else "paused"))
    return jsonify({"running": S["running"]})


@app.post("/api/reset")
def reset():
    S["balance"] = S["starting_balance"]
    S["opportunities"] = 0
    S["paper_trades"] = 0
    S["best"] = None
    S["quotes"] = []
    S["last_error"] = None
    log("Paper balance reset")
    return jsonify({"ok": True})


if __name__ == "__main__":
    ensure_scanner_started()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
