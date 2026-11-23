"""不同判斷頻率的槓桿網格對照。用法: grid_tf_run.py <60|10m|5m> SYM... (縮小參數組以控制時間)"""
import sys, itertools
from multiprocessing import Pool
import numpy as np, pandas as pd
from bot.grid_lev import run_grid_lev

TF = sys.argv[1]; SYMS = sys.argv[2:]
PER_H = {"60": 1, "10m": 6, "5m": 12}[TF]
GRID = list(itertools.product([0.10, 0.20], [10, 20], [0.05], [30, 60]))   # w, n, stop, reset_days
LEVS = (1, 3)
DF = FD = None

def job(a):
    p, lev = a
    r = run_grid_lev(DF, FD, p[0], p[1], p[2], p[3] * 24 * PER_H, lev=lev)
    return p, lev, float(r.iloc[-1] - 1), float((r / r.cummax() - 1).min()), r.attrs["liqs"], r.attrs["stops"]

if __name__ == "__main__":
    for sym in SYMS:
        f = f"data/{sym}_{TF}.pkl"
        DF = pd.read_pickle(f); FD = pd.read_pickle(f"data/{sym}_funding.pkl")
        with Pool(8) as pool:
            out = pool.map(job, [(p, l) for l in LEVS for p in GRID])
        print(f"\n== {sym} {TF} {len(DF)}根 | 持有 {DF['close'].iloc[-1]/DF['close'].iloc[0]-1:+.1%}")
        for lev in LEVS:
            o = [x for x in out if x[1] == lev]; t = np.array([x[2] for x in o])
            print(f"  {lev}x: 賺錢 {int((t>0).sum())}/{len(o)} | 報酬中位數 {np.median(t):+.1%} 最好 {t.max():+.1%} 最差 {t.min():+.1%} | "
                  f"回撤中位數 {np.median([x[3] for x in o]):.1%} | 強平組 {sum(x[4]>0 for x in o)}/{len(o)} | 停損中位數 {int(np.median([x[5] for x in o]))} 次")
