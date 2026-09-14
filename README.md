# Solana Arbitrage Paper Bot

This is a **paper-trading** starter bot. It does NOT use a private key and does NOT place real trades.

## What it does
- Polls Jupiter's public quote API for configured token pairs.
- Looks for a simple two-leg price difference using two quote directions.
- Includes configurable paper fees/slippage.
- Starts with a virtual balance (default $1 equivalent in USDC).
- Logs simulated opportunities/trades.
- Exposes a small HTTP dashboard/API.
- Includes a Render deployment file.

## Important
This is an educational simulator, not a profitable trading system. A quote is not a guarantee that a live transaction can execute at that price. Do not add real wallet keys to this version.

## Local run
Python 3.11+ recommended.

```bash
pip install -r requirements.txt
python app.py
```

Open http://localhost:10000

## Render
Create a Web Service from this repository/ZIP after uploading it to GitHub.
- Build Command: `pip install -r requirements.txt`
- Start Command: `python app.py`

Set environment variables:
- `POLL_SECONDS=5`
- `STARTING_USDC=1`
- `TRADE_FRACTION=0.10`
- `MIN_NET_PROFIT_PCT=0.10`
- `PAPER_FEE_PCT=0.30`
- `PAPER_SLIPPAGE_PCT=0.30`

The simulator uses public market quotes and virtual money only.
