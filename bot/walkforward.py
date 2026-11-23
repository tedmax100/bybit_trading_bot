"""Walk-forward:滾動視窗,訓練期選參數,下一段樣本外驗證,只看樣本外成績。"""
import itertools

import numpy as np
import pandas as pd

from .config import SETTINGS
from .strategies.trend_spot import TrendSpot

GRID = list(itertools.product([10, 20, 40, 55], [5, 10, 20], [2.0, 3.0, 4.0]))  # entry_n, exit_n, atr_mult


def strat_returns(df: pd.DataFrame, p, fee=SETTINGS.spot_fee, slip=SETTINGS.slippage) -> pd.Series:
    pos = TrendSpot(*p[:2], 14, p[2]).positions(df).shift().fillna(0.0)
    turn = pos.diff().abs().fillna(pos.abs())
    return pos * df["close"].pct_change().fillna(0.0) - turn * (fee + slip)


def sharpe(r: pd.Series) -> float:
    return float(r.mean() / r.std()) if r.std() > 0 else -9.0


def walk_forward(df: pd.DataFrame, train_bars=6 * 365, test_bars=6 * 90, warm=100):
    oos, picks, i = [], [], 0
    while i + train_bars + test_bars <= len(df):
        tr = df.iloc[i:i + train_bars]
        te = df.iloc[i + train_bars - warm:i + train_bars + test_bars]   # 預熱段讓指標有值
        best = max(GRID, key=lambda p: sharpe(strat_returns(tr, p)))
        r = strat_returns(te, best).iloc[warm:]
        oos.append(r)
        picks.append((te.index[warm].date(), best, float((1 + r).prod() - 1)))
        i += test_bars
    return pd.concat(oos), picks


def robustness(df: pd.DataFrame) -> dict:
    tot = [float((1 + strat_returns(df, p)).prod() - 1) for p in GRID]
    return {"grid": len(GRID), "profitable": int(sum(t > 0 for t in tot)),
            "median": float(np.median(tot)), "best": max(tot), "worst": min(tot)}
