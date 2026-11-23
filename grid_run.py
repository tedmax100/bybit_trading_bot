import itertools
import numpy as np, pandas as pd
from bot.grid import run_grid
from bot.walkforward import sharpe

GRID = list(itertools.product([0.10, 0.20, 0.30], [10, 20], [0.05, 0.10], [24*30, 24*60]))   # w, n, stop, reset_bars
def rets(df, p): return run_grid(df, *p).pct_change().fillna(0.0)

for sym in ["BTCUSDT", "ETHUSDT"]:
    df = pd.read_pickle(f"data/{sym}_60.pkl")
    tr_n, te_n = 24*365, 24*90
    oos, picks, i = [], [], 0
    while i + tr_n + te_n <= len(df):
        best = max(GRID, key=lambda p: sharpe(rets(df.iloc[i:i+tr_n], p)))
        r = rets(df.iloc[i+tr_n:i+tr_n+te_n], best); oos.append(r); picks.append(float((1+r).prod()-1)); i += te_n
    o = pd.concat(oos); eq = (1+o).cumprod()
    hold = df["close"].iloc[-1] / df["close"].loc[o.index[0]] - 1
    hr = df["close"].loc[o.index[0]:].pct_change().fillna(0); heq = (1+hr).cumprod()
    full = {p: run_grid(df, *p) for p in GRID}
    tot = [float(e.iloc[-1]/e.iloc[0]-1) for e in full.values()]
    dds = [float((e/e.cummax()-1).min()) for e in full.values()]
    print(f"\n== {sym} 1H 網格 | 樣本外 {len(picks)} 段,賺錢 {sum(x>0 for x in picks)} 段")
    print(f"   樣本外總報酬 {eq.iloc[-1]-1:+.1%}  最大回撤 {(eq/eq.cummax()-1).min():.1%} | 同期持有 {hold:+.1%} 回撤 {(heq/heq.cummax()-1).min():.1%}")
    print(f"   全樣本 {len(GRID)} 組:賺錢 {sum(x>0 for x in tot)} 組,報酬中位數 {np.median(tot):+.1%} (最好 {max(tot):+.1%} / 最差 {min(tot):+.1%}),回撤中位數 {np.median(dds):.1%}")
    b = max(full, key=lambda p: full[p].iloc[-1])
    print(f"   全樣本最佳 w={b[0]:.0%} n={b[1]} stop={b[2]:.0%} reset={b[3]//24}天: {full[b].iloc[-1]-1:+.1%} 回撤 {(full[b]/full[b].cummax()-1).min():.1%} 停損{full[b].attrs['stops']}次")
