import itertools
import numpy as np, pandas as pd
from bot.grid_lev import run_grid_lev

GRID = list(itertools.product([0.10, 0.20, 0.30], [10, 20], [0.05, 0.10], [24*30, 24*60]))
import sys
for sym in sys.argv[1:] or ["BTCUSDT", "ETHUSDT"]:
    df = pd.read_pickle(f"data/{sym}_60.pkl"); fd = pd.read_pickle(f"data/{sym}_funding.pkl")
    hold = df["close"].iloc[-1] / df["close"].iloc[0] - 1
    hd = (df["close"] / df["close"].cummax() - 1).min()
    print(f"\n== {sym} 1H 永續網格 {df.index[0].date()}~{df.index[-1].date()} | 持有 {hold:+.1%} 回撤 {hd:.1%}")
    for lev in (1, 2, 3):
        res = {p: run_grid_lev(df, fd, *p, lev=lev) for p in GRID}
        tot = np.array([r.iloc[-1] - 1 for r in res.values()])
        dd = np.array([(r / r.cummax() - 1).min() for r in res.values()])
        liq = np.array([r.attrs["liqs"] for r in res.values()])
        b = max(res, key=lambda p: res[p].iloc[-1])
        print(f"  {lev}x: 賺錢 {int((tot>0).sum())}/24 | 報酬中位數 {np.median(tot):+.1%} 最好 {tot.max():+.1%} 最差 {tot.min():+.1%} | "
              f"回撤中位數 {np.median(dd):.1%} | 有被強平的組 {int((liq>0).sum())}/24 (共 {liq.sum()} 次) | "
              f"最佳 w={b[0]:.0%} n={b[1]} stop={b[2]:.0%} reset={b[3]//24}d 回撤 {(res[b]/res[b].cummax()-1).min():.1%}")
