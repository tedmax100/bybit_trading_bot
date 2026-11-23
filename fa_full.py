"""資金費率套利完整回測:用真實『現貨價』與『永續價』(含基差),日線,模擬 1.5x 保證金、強平、重新置中、funding 轉負退出。"""
import numpy as np, pandas as pd
from pathlib import Path
from bot import exchange

SPOT_C, PERP_C = 0.001 + 0.0005, 0.00055 + 0.0005       # 單邊成本 (手續費+滑價)
MMR = 0.005

def load(coin):
    sym = coin + "USDT"
    f = Path(f"data/{sym}_D_perp.pkl")
    if f.exists(): perp = pd.read_pickle(f)
    else:
        perp = exchange.fetch_klines(sym, "D", "linear", start_ms=int(pd.Timestamp("2021-06-01", tz="UTC").timestamp()*1000)); perp.to_pickle(f)
    spot = pd.read_pickle(f"data/{sym}_D.pkl")
    fund = pd.read_pickle(f"data/{sym}_funding.pkl"); fund = fund.groupby(fund.index.floor("D")).sum()
    d = pd.DataFrame({"s": spot["close"], "p": perp["close"], "ph": perp["high"], "f": fund}).dropna(subset=["s", "p"])
    d["f"] = d["f"].fillna(0.0)
    return d[d.index >= pd.Timestamp("2021-11-10", tz="UTC")]

def sim(d, lev=1.5, recenter=0.30, exit_neg=False, lookback=7):
    V, qty, entry, margin, stats = 1.0, 0.0, 0.0, 0.0, dict(liq=0, rec=0, trades=0, days_in=0)
    cash, eq, avg = 1.0, [], d["f"].rolling(lookback).mean().shift()
    def open_(i):
        nonlocal cash, qty, entry, margin
        N = cash / (1 + 1 / lev)
        q = N / d["s"].iloc[i] / (1 + SPOT_C)
        qty, entry = q, d["p"].iloc[i] * (1 - 0.0005)
        margin = q * entry / lev
        cash -= q * d["s"].iloc[i] * (1 + SPOT_C) + margin + q * entry * 0.00055
        stats["trades"] += 1
    def close_(i):
        nonlocal cash, qty, margin
        px = d["p"].iloc[i] * (1 + 0.0005)
        cash += qty * d["s"].iloc[i] * (1 - SPOT_C) + margin + qty * (entry - px) - qty * px * 0.00055
        qty = margin = 0.0
    for i in range(len(d)):
        if qty > 0:
            stats["days_in"] += 1
            cash += qty * d["p"].iloc[i] * d["f"].iloc[i]                     # funding 入帳
            if d["ph"].iloc[i] >= entry * (1 + 1 / lev) / (1 + MMR):          # 強平:保證金歸零,現貨賣出
                cash += qty * d["s"].iloc[i] * (1 - SPOT_C); qty = margin = 0.0; stats["liq"] += 1
            elif d["p"].iloc[i] >= entry * (1 + recenter):
                close_(i); open_(i); stats["rec"] += 1
            elif exit_neg and avg.iloc[i] == avg.iloc[i] and avg.iloc[i] < 0:
                close_(i)
        elif i > 0 and (not exit_neg or (avg.iloc[i] == avg.iloc[i] and avg.iloc[i] > 0)):
            open_(i)
        eq.append(cash + (qty * d["s"].iloc[i] + margin + qty * (entry - d["p"].iloc[i]) if qty > 0 else 0))
    return pd.Series(eq, index=d.index), stats

def summ(e, st):
    r = e.pct_change().fillna(0); yrs = len(e) / 365
    return dict(年化=e.iloc[-1] ** (1 / yrs) - 1, 回撤=(e / e.cummax() - 1).min(), 最差30天=(e / e.shift(30) - 1).min(),
                在場=st["days_in"] / len(e), 爆倉=st["liq"], 置中=st["rec"], 開倉=st["trades"])

res = {}
for coin in ["BTC", "ETH"]:
    d = load(coin)
    basis = (d["p"] / d["s"] - 1)
    print(f"\n== {coin} {d.index[0].date()}~{d.index[-1].date()} | 基差 平均 {basis.mean():+.3%} 標準差 {basis.std():.3%} 最大偏離 {basis.abs().max():.2%}")
    for nm, kw in [("原回測設定: 2x、不置中", dict(lev=2.0, recenter=9)), ("模擬設定: 1.5x、漲30%置中", dict()),
                   ("模擬設定 + funding 7日均值<0 退出", dict(exit_neg=True))]:
        e, st = sim(d, **kw); res[(coin, nm)] = e
        s = summ(e, st)
        print(f"  {nm:34s} 年化 {s['年化']:+.2%} 回撤 {s['回撤']:.2%} 最差30天 {s['最差30天']:+.2%} 在場 {s['在場']:.0%} 爆倉{s['爆倉']} 置中{s['置中']} 開倉{s['開倉']}")
pd.to_pickle(res, "data/fa_full_res.pkl")
