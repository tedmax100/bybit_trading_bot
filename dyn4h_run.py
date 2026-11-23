"""D 策略 (趨勢分數 × 波動目標, BTC70/ETH30):
日線基準 vs 4小時決策 vs 4小時+EMA21/50濾網 vs 4小時+EMA濾網+三段式防護盾。
從空手起算 (不佔視窗第一根的便宜),調整門檻按各檔額度計 (與模擬帳戶一致)。
用法: .venv/bin/python dyn4h_run.py [refresh]
"""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from bot import exchange
from bot.config import SETTINGS
from bot.strategies.dynamic import trend_vol

COST = SETTINGS.spot_fee + SETTINGS.slippage
MIX = {"BTC": 0.7, "ETH": 0.3}
DAY = dict(mas=(50, 100, 200), moms=(30, 90, 180), n=30)                         # 單位:天


def load_full(coin, iv, refresh):
    f = Path(f"data/{coin}USDT_{iv}_spot.pkl")
    if refresh or not f.exists():
        df = exchange.fetch_klines(coin + "USDT", iv, "spot", start_ms=int(time.time() * 1000) - 1850 * 86_400_000)
        df.to_pickle(f)
    df = pd.read_pickle(f)
    step = pd.Timedelta(days=1) if iv == "D" else pd.Timedelta(hours=4)
    return df[df.index + step <= pd.Timestamp.now(tz="UTC")]                        # 只用已收盤的 K 線


def band(w, thr):
    out, cur = w.to_numpy().copy(), 0.0
    for i, t in enumerate(np.nan_to_num(out)):
        if abs(t - cur) >= thr or (t == 0 and cur != 0):
            cur = t
        out[i] = cur
    return pd.Series(out, index=w.index)


def weights(C, k, thr, params=DAY, every=1, ema_short=False):
    """k = 一天幾根 K 線;params 以天為單位再乘 k。every>1 = 每 every 根才決策一次。"""
    W = {}
    for c, mix in MIX.items():
        w = trend_vol(C[c], n=params["n"] * k, ppy=365 * k, mas=tuple(m * k for m in params["mas"]),
                      moms=tuple(m * k for m in params["moms"]), ema_short=ema_short)
        if every > 1:
            w = w.where(pd.Series(np.arange(len(w)) % every == every - 1, index=w.index)).ffill()
        W[c] = band(w, thr) * mix
    return pd.DataFrame(W)


def weights_shield(H_dfs, k=6, thr=0.20, ema_short=True, sl=0.08, be=0.06, tp=0.12, pb=0.03):
    """4小時 + 短期EMA濾網 + 三段式防護盾 (Hard SL / Break-Even / Trailing TP)。"""
    W = {}
    for c, mix in MIX.items():
        df = H_dfs[c]
        close = df["close"]
        raw_w = trend_vol(close, n=DAY["n"] * k, ppy=365 * k, mas=tuple(m * k for m in DAY["mas"]),
                          moms=tuple(m * k for m in DAY["moms"]), ema_short=ema_short)
        c_vals = close.values
        h_vals = df["high"].values if "high" in df.columns else c_vals
        l_vals = df["low"].values if "low" in df.columns else c_vals
        raw_vals = raw_w.values
        n = len(df)
        out = np.zeros(n)
        in_pos = False
        cur_w = 0.0
        entry_px = 0.0
        peak_px = 0.0

        for i in range(1200, n):
            target = raw_vals[i] if not np.isnan(raw_vals[i]) else 0.0
            price = c_vals[i]

            if in_pos:
                peak_px = max(peak_px, h_vals[i])
                pnl = (price - entry_px) / entry_px
                peak_pnl = (peak_px - entry_px) / entry_px

                is_sl = (l_vals[i] - entry_px) / entry_px <= -sl
                is_be = peak_pnl >= be and pnl <= 0.003
                is_tp = peak_pnl >= tp and (peak_pnl - pnl) >= pb

                if target == 0.0 or is_sl or is_be or is_tp:
                    in_pos = False
                    cur_w = 0.0
                    entry_px = 0.0
                    peak_px = 0.0
                else:
                    if abs(target - cur_w) >= thr:
                        cur_w = target
                out[i] = cur_w
            else:
                if target > 0.0:
                    in_pos = True
                    cur_w = target
                    entry_px = price
                    peak_px = price
                    out[i] = cur_w
                else:
                    out[i] = 0.0
        W[c] = pd.Series(out, index=close.index) * mix
    return pd.DataFrame(W)


def run(C, W, lo, hi, cost=COST):
    W = W.copy(); W[W.index < lo] = 0.0                                          # 視窗起點從空手開始
    P = W.shift().fillna(0.0)
    turn = P.diff().abs().sum(axis=1).fillna(0.0)
    r = ((P * C.pct_change().fillna(0.0)).sum(axis=1) - turn * cost).loc[lo:hi]
    eq = (1 + r).cumprod(); yrs = (r.index[-1] - r.index[0]).days / 365
    d = eq.resample("D").last()
    return dict(總報酬=eq.iloc[-1] - 1, 年化=eq.iloc[-1] ** (1 / yrs) - 1, 最大回撤=(eq / eq.cummax() - 1).min(),
                夏普=d.pct_change().mean() / d.pct_change().std() * np.sqrt(365), 平均曝險=P.loc[lo:hi].sum(axis=1).mean(),
                年換手=turn.loc[lo:hi].sum() / yrs, 調整次數=int((P.loc[lo:hi].diff().abs() > 0).sum().sum()))


FMT = {k: (lambda v: f"{v:+.1%}") for k in ["總報酬", "年化", "最大回撤"]} | {
    "夏普": lambda v: f"{v:.2f}", "平均曝險": lambda v: f"{v:.0%}", "年換手": lambda v: f"{v:.1f}x", "調整次數": lambda v: f"{v:.0f}"}


def main():
    rf = "refresh" in sys.argv
    D_dfs = {c: load_full(c, "D", rf) for c in MIX}
    H_dfs = {c: load_full(c, "240", rf) for c in MIX}
    D = pd.DataFrame({c: D_dfs[c]["close"] for c in MIX}).dropna()
    H = pd.DataFrame({c: H_dfs[c]["close"] for c in MIX}).dropna()
    end = D.index[-1] + pd.Timedelta(days=1)                                     # 兩種頻率用同一段時間
    mid = end - pd.Timedelta(days=730)
    first = H.index[0] + pd.Timedelta(days=205)
    book = {
        "每天一次 (現行日線基準)": (D, weights(D, 1, 0.05)),
        "每4小時 同參數 門檻20%": (H, weights(H, 6, 0.20)),
        "每4小時 + EMA21/50 濾網 (門檻20%)": (H, weights(H, 6, 0.20, ema_short=True)),
        "每4小時 + EMA濾網 + 三段式防護盾": (H, weights_shield(H_dfs, 6, 0.20, ema_short=True, sl=0.08, be=0.06, tp=0.12, pb=0.03)),
    }
    print(f"日線 {len(D)} 根、4小時線 {len(H)} 根,到 {H.index[-1]};成本每次換手 {COST:.2%}")
    for title, lo, hi, cost in [(f"最近兩年 {mid.date()} ~ {end.date()}", mid, end, COST),
                                (f"前一段 {first.date()} ~ {mid.date()}", first, mid - pd.Timedelta(hours=4), COST),
                                ("最近兩年,成本加倍 (0.30%)", mid, end, COST * 2)]:
        print(f"\n=== {title} ===")
        print(pd.DataFrame({k: run(C, W, lo, hi, cost) for k, (C, W) in book.items()}).T.to_string(formatters=FMT))


if __name__ == "__main__":
    main()
