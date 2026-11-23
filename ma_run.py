import time
from pathlib import Path
import numpy as np, pandas as pd
from bot import exchange
from bot.config import SETTINGS
from bot.strategies.ma_filter import MAFilter

COST = SETTINGS.spot_fee + SETTINGS.slippage
COINS = ["BTC", "ETH", "LINK", "SOL", "AAVE", "XRP"]
NS = [50, 75, 100, 150, 200, 250, 300]

def load(coin):
    f = Path(f"data/{coin}USDT_D.pkl")
    if f.exists(): return pd.read_pickle(f)
    end = int(time.time() * 1000)
    df = exchange.fetch_klines(coin + "USDT", "D", "spot", start_ms=end - 2600 * 86_400_000)
    df.to_pickle(f); return df

def rets(df, n, band=0.0):
    pos = MAFilter(n, band).positions(df).shift().fillna(0.0)
    turn = pos.diff().abs().fillna(pos.abs())
    return pos * df["close"].pct_change().fillna(0.0) - turn * COST, turn

def stats(r):
    eq = (1 + r).cumprod(); yrs = len(r) / 365
    return dict(tot=eq.iloc[-1] - 1, cagr=eq.iloc[-1] ** (1 / yrs) - 1, dd=(eq / eq.cummax() - 1).min())

data = {c: load(c) for c in COINS}
print("資料區間:", {c: f"{d.index[0].date()}~{len(d)}天" for c, d in data.items()})
print("\n=== 主設定 N=200,無遲滯帶 (從有 200 日均線起算,與買進持有同期比較) ===")
print(f"{'幣':5s} {'策略報酬':>9s} {'策略回撤':>9s} {'持有報酬':>9s} {'持有回撤':>9s} {'在場':>6s} {'交易':>5s}")
for c, df in data.items():
    r, t = rets(df, 200); r, t = r.iloc[200:], t.iloc[200:]
    bh = df["close"].pct_change().fillna(0).iloc[200:]
    s, b = stats(r), stats(bh)
    inm = MAFilter(200).positions(df).iloc[200:].mean()
    print(f"{c:5s} {s['tot']:+9.0%} {s['dd']:9.1%} {b['tot']:+9.0%} {b['dd']:9.1%} {inm:6.0%} {int((t>0).sum()//2):5d}")

print("\n=== 穩健性:各均線長度的最大回撤 / 報酬 (同期持有回撤在上一表) ===")
print(f"{'幣':5s}" + "".join(f"{n:>14d}" for n in NS))
for c, df in data.items():
    row = f"{c:5s}"
    for n in NS:
        r, _ = rets(df, n); r = r.iloc[300:]; s = stats(r)
        row += f"{s['dd']:8.0%}/{s['tot']:+5.0%} "
    print(row)

print("\n=== 前後半段切開 (N=200):策略回撤 vs 持有回撤 ===")
for c, df in data.items():
    r, _ = rets(df, 200); r = r.iloc[200:]; bh = df["close"].pct_change().fillna(0).iloc[200:]
    h = len(r) // 2
    a, b = stats(r.iloc[:h]), stats(r.iloc[h:]); ba, bb = stats(bh.iloc[:h]), stats(bh.iloc[h:])
    print(f"{c:5s} 前半 策略 {a['tot']:+5.0%}/{a['dd']:5.0%} 持有 {ba['tot']:+5.0%}/{ba['dd']:5.0%} | 後半 策略 {b['tot']:+5.0%}/{b['dd']:5.0%} 持有 {bb['tot']:+5.0%}/{bb['dd']:5.0%}")

print("\n=== 遲滯帶 (N=200, BTC/ETH):交易次數 vs 報酬 ===")
for c in ["BTC", "ETH"]:
    for band in [0, 0.02, 0.05]:
        r, t = rets(data[c], 200, band); s = stats(r.iloc[300:])
        print(f"{c} band={band:.0%} 報酬 {s['tot']:+.0%} 回撤 {s['dd']:.0%} 交易 {int((t.iloc[300:]>0).sum()//2)}")
