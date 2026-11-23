import sys
import numpy as np, pandas as pd
from pathlib import Path
from pybit.unified_trading import HTTP
from bot.funding_arb import fetch_funding, run
from bot import exchange

COINS = ["BTC","ETH","LINK","XRP","SOL","ENA","AAVE","NEAR","BNB","UNI","ARB","LTC","TRX","ONDO","SUI","XLM","AVAX"]
c = HTTP(testnet=False)
perps = {x["symbol"] for x in c.get_instruments_info(category="linear", limit=1000)["result"]["list"]}
rows = []
for coin in COINS:
    sym = coin + "USDT"
    if sym not in perps:
        print(f"{coin}: 無 USDT 永續,跳過"); continue
    f = Path(f"data/{sym}_funding.pkl")
    s = pd.read_pickle(f) if f.exists() else fetch_funding(sym)
    if not f.exists(): s.to_pickle(f)
    d = exchange.fetch_klines(sym, "D", "spot", start_ms=int(s.index[0].timestamp()*1000) - 86_400_000*8)
    px = d["close"]
    yrs = (s.index[-1]-s.index[0]).days/365
    r = run(s)
    roll30 = s.rolling(int(30*86400/ s.index.to_series().diff().median().total_seconds())).sum()
    rally7 = (px.shift(-7)/px - 1).max()
    rows.append(dict(coin=coin, yrs=round(yrs,1), 間隔h=round(s.index.to_series().diff().median().total_seconds()/3600),
        年化funding=s.sum()/yrs, 套利年化=r["annualized"], 回撤=r["max_drawdown"], 負費率占比=r["neg_funding_periods"],
        最差30天=roll30.min(), 近1年funding=s[s.index>s.index[-1]-pd.Timedelta(days=365)].sum(),
        七日最大漲幅=rally7, 日均成交額M=float(d["turnover"].tail(30).median()/1e6)))
df = pd.DataFrame(rows).set_index("coin")
pd.set_option("display.width", 250)
print(df.to_string(formatters={k:(lambda v:f"{v:+.1%}") for k in ["年化funding","套利年化","回撤","最差30天","近1年funding","七日最大漲幅","負費率占比"]}|{"日均成交額M":lambda v:f"{v:.1f}"}))
