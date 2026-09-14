import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from flask import Flask, jsonify, render_template

app = Flask(__name__)

# Paper-only scanner. No wallet, signing or real transactions.
QUOTE = "https://quote-api.jup.ag/v6/quote"
SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
DEXES = ["Raydium", "Orca Whirlpool", "Meteora DLMM", "Lifinity"]


def num(key, default):
    try:
        return float(os.getenv(key, default))
    except (TypeError, ValueError):
        return float(default)


POLL = max(5, int(num("POLL_SECONDS", "10")))
START = max(0.01, num("STARTING_USDC", "1"))
FRACTION = min(1.0, max(0.01, num("TRADE_FRACTION", ".10")))
MINP = num("MIN_NET_PROFIT_PCT", ".10")
FEE = num("PAPER_FEE_PCT", ".30")
SLIP = num("PAPER_SLIPPAGE_PCT", ".30")
PROBE = max(0.01, num("PROBE_USDC", "1"))

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
}


def log(message):
    S["logs"].insert(0, time.strftime("%H:%M:%S") + "  " + message)
    del S["logs"][50:]


def quote(input_mint, output_mint, amount_raw, dex):
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(int(amount_raw)),
        "slippageBps": "50",
        "swapMode": "ExactIn",
        "dexes": dex,
        "onlyDirectRoutes": "true",
    }
    response = requests.get(QUOTE, params=params, timeout=8)
    response.raise_for_status()
    return response.json()


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
    amount = min(PROBE, max(0.01, S["balance"] * FRACTION))

    buys = []
    with ThreadPoolExecutor(max_workers=len(DEXES)) as pool:
        futures = [pool.submit(buy_quote, dex, amount) for dex in DEXES]
        for future in as_completed(futures):
            result, error = future.result()
            if result:
                buys.append(result)
            elif error:
                log(error)

    buys.sort(key=lambda item: item["sol"], reverse=True)
    if not buys:
        raise RuntimeError("No buy quotes returned")

    jobs = [(buy, dex) for buy in buys for dex in DEXES if dex != buy["dex"]]
    sells = []
    with ThreadPoolExecutor(max_workers=min(16, len(jobs))) as pool:
        futures = [pool.submit(sell_quote, buy, dex) for buy, dex in jobs]
        for future in as_completed(futures):
            result = future.result()
            if result:
                sells.append(result)

    if not sells:
        raise RuntimeError("No cross-DEX sell quotes returned")

    best = max(sells, key=lambda item: item["usdc"])
    gross_pct = (best["usdc"] / amount - 1) * 100
    netout = best["usdc"] * (1 - FEE / 100) * (1 - SLIP / 100)
    net = netout - amount
    net_pct = (netout / amount - 1) * 100

    now = time.strftime("%H:%M:%S")
    S["quotes"] = buys
    S["last_scan"] = now
    S["last_error"] = None
    S["best"] = {
        "input": amount,
        "buy": best["buy"],
        "sell": best["sell"],
        "gross": best["usdc"],
        "gross_pct": gross_pct,
        "netout": netout,
        "net": net,
        "net_pct": net_pct,
        "time": now,
    }

    if net_pct >= MINP:
        S["opportunities"] += 1
        profit = net * (min(amount, S["balance"] * FRACTION) / amount)
        S["balance"] += profit
        S["paper_trades"] += 1
        log(
            f"PAPER TRADE {best['buy']} -> {best['sell']} | "
            f"+${profit:.8f} ({net_pct:.4f}%)"
        )
    else:
        log(f"No trade: best net {net_pct:.4f}% < {MINP:.4f}%")


def worker():
    log("Scanner worker started")
    while True:
        if S["running"] and not S["scan_in_progress"]:
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
            "settings": {
                "poll": POLL,
                "fraction": FRACTION,
                "min_profit_pct": MINP,
                "fee_pct": FEE,
                "slippage_pct": SLIP,
                "probe_usdc": PROBE,
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
