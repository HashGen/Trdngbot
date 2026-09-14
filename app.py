import os,time,threading,requests
from flask import Flask,jsonify,render_template
app=Flask(__name__)
QUOTE="https://quote-api.jup.ag/v6/quote"
SOL="So11111111111111111111111111111111111111112"
USDC="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
DEXES=["Raydium","Orca Whirlpool","Meteora DLMM","Lifinity"]

def num(k,d):
    try:return float(os.getenv(k,d))
    except:return float(d)
POLL=max(5,int(os.getenv("POLL_SECONDS","10")))
START=num("STARTING_USDC","1")
FRACTION=min(1,max(.01,num("TRADE_FRACTION",".10")))
MINP=num("MIN_NET_PROFIT_PCT",".10")
FEE=num("PAPER_FEE_PCT",".30")
SLIP=num("PAPER_SLIPPAGE_PCT",".30")
PROBE=max(.01,num("PROBE_USDC","1"))
S={"running":True,"balance":START,"starting_balance":START,"cycles":0,
   "opportunities":0,"paper_trades":0,"last_scan":None,"best":None,
   "quotes":[],"logs":[]}

def log(x):
    S["logs"].insert(0,time.strftime("%H:%M:%S")+"  "+x);del S["logs"][50:]
def q(inp,out,amt,dex):
    p={"inputMint":inp,"outputMint":out,"amount":str(int(amt)),
       "slippageBps":"50","swapMode":"ExactIn","dexes":dex,"onlyDirectRoutes":"true"}
    r=requests.get(QUOTE,params=p,timeout=8);r.raise_for_status();return r.json()

def scan():
    amount=min(PROBE,max(.01,S["balance"]*FRACTION))
    buys=[]
    for d in DEXES:
        try:
            x=q(USDC,SOL,amount*1_000_000,d);o=int(x.get("outAmount","0"))
            if o: buys.append({"dex":d,"sol_raw":o,"sol":o/1e9,
                               "impact":float(x.get("priceImpactPct") or 0)})
        except: log(d+": quote unavailable")
    if not buys: raise RuntimeError("No buy quotes returned")
    sells=[]
    for b in buys:
        for d in DEXES:
            try:
                x=q(SOL,USDC,b["sol_raw"],d);o=int(x.get("outAmount","0"))
                if o:sells.append({"buy":b["dex"],"sell":d,"usdc":o/1e6})
            except: pass
    if not sells: raise RuntimeError("No sell quotes returned")
    best=max(sells,key=lambda x:x["usdc"])
    gross=(best["usdc"]/amount-1)*100
    netout=best["usdc"]*(1-FEE/100)*(1-SLIP/100)
    net=netout-amount; pct=(netout/amount-1)*100
    S["quotes"]=buys;S["last_scan"]=time.strftime("%H:%M:%S")
    S["best"]={"input":amount,"buy":best["buy"],"sell":best["sell"],
               "gross":best["usdc"],"gross_pct":gross,"netout":netout,
               "net":net,"net_pct":pct,"time":S["last_scan"]}
    if pct>=MINP:
        S["opportunities"]+=1
        profit=net*(min(amount,S["balance"]*FRACTION)/amount)
        S["balance"]+=profit;S["paper_trades"]+=1
        log(f"PAPER TRADE {best['buy']} -> {best['sell']} | +${profit:.8f} ({pct:.4f}%)")
    else: log(f"No trade: best net {pct:.4f}% < {MINP:.4f}%")

def worker():
    while True:
        if S["running"]:
            S["cycles"]+=1
            try:scan()
            except Exception as e:log("Scan error: "+str(e)[:100])
        time.sleep(POLL)
@app.get("/")
def home():return render_template("index.html")
@app.get("/api/status")
def status():
    return jsonify({**S,"profit":S["balance"]-S["starting_balance"],
      "settings":{"poll":POLL,"fraction":FRACTION,"min_profit_pct":MINP,
      "fee_pct":FEE,"slippage_pct":SLIP,"probe_usdc":PROBE,"dexes":DEXES}})
@app.post("/api/toggle")
def toggle():
    S["running"]=not S["running"];log("Bot "+("started" if S["running"] else "paused"))
    return jsonify({"running":S["running"]})
@app.post("/api/reset")
def reset():
    S["balance"]=S["starting_balance"];S["opportunities"]=S["paper_trades"]=0
    S["best"]=None;S["quotes"]=[];log("Paper balance reset");return jsonify({"ok":True})
threading.Thread(target=worker,daemon=True).start()
if __name__=="__main__":app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))
