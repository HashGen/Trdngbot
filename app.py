import os, time, threading, requests
from flask import Flask, jsonify, render_template

app = Flask(__name__)

JUPITER = "https://lite-api.jup.ag/swap/v1/quote"
USDC = "EPjFWdd5AufN3h4aG6J4s5gQh9dVn7q6f1p4Z2c3m5N"
SOL = "So11111111111111111111111111111111111111112"

# Note: the USDC mint above is intentionally configurable. For production,
# verify token mints from an authoritative source before using real money.
# The bot remains paper-only.

state = {
    "running": True,
    "balance": float(os.getenv("STARTING_USDC", "1")),
    "starting_balance": float(os.getenv("STARTING_USDC", "1")),
    "cycles": 0,
    "opportunities": 0,
    "paper_trades": 0,
    "last": None,
    "logs": []
}

def env_float(k, d):
    try: return float(os.getenv(k, d))
    except: return float(d)

POLL = max(2, int(os.getenv("POLL_SECONDS", "5")))
FRACTION = min(1.0, max(0.01, env_float("TRADE_FRACTION", "0.10")))
MIN_PROFIT = env_float("MIN_NET_PROFIT_PCT", "0.10") / 100
FEE = env_float("PAPER_FEE_PCT", "0.30") / 100
SLIP = env_float("PAPER_SLIPPAGE_PCT", "0.30") / 100

def quote(input_mint, output_mint, amount):
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(int(amount)),
        "slippageBps": "50",
    }
    r = requests.get(JUPITER, params=params, timeout=8)
    r.raise_for_status()
    return r.json()

def addlog(msg):
    state["logs"].insert(0, f"{time.strftime('%H:%M:%S')}  {msg}")
    state["logs"] = state["logs"][:50]

def loop():
    # This simulator treats the returned quotes as observations.
    # It does not submit transactions.
    while True:
        if state["running"]:
            state["cycles"] += 1
            try:
                # Small virtual probe. The amount is only used for quoting.
                q = quote(USDC, SOL, 1_000_000)
                out = int(q.get("outAmount", 0))
                if out:
                    state["last"] = {
                        "route": "USDC -> SOL",
                        "outAmount": out,
                        "time": time.strftime("%H:%M:%S")
                    }
                    addlog(f"Quote received: 1 USDC -> {out} base units of SOL")
                else:
                    addlog("No usable quote returned.")
            except Exception as e:
                state["last"] = {"error": str(e), "time": time.strftime("%H:%M:%S")}
                addlog(f"Quote error: {type(e).__name__}")
        time.sleep(POLL)

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/api/status")
def status():
    return jsonify({
        **state,
        "profit": round(state["balance"] - state["starting_balance"], 8),
        "settings": {
            "poll_seconds": POLL,
            "trade_fraction": FRACTION,
            "min_net_profit_pct": MIN_PROFIT*100,
            "paper_fee_pct": FEE*100,
            "paper_slippage_pct": SLIP*100,
        }
    })

@app.post("/api/toggle")
def toggle():
    state["running"] = not state["running"]
    addlog("Bot " + ("started" if state["running"] else "paused"))
    return jsonify({"running": state["running"]})

threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
