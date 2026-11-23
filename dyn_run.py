"""動態策略回測 (日線、現貨只做多)。主視窗 = 最近兩年;前一段作為對照,避免只挑兩年內好看的參數。
t 日收盤算出目標權重 -> t+1 日持有;每次調整按換手量付手續費+滑價。
用法: .venv/bin/python dyn_run.py [refresh]
"""
import sys
import time

import numpy as np
import pandas as pd

from bot import exchange
from bot.config import SETTINGS
from bot.strategies.dynamic import ensemble, ma_on, vol, vol_scale

COST = SETTINGS.spot_fee + SETTINGS.slippage
COINS = ["BTC", "ETH", "LINK", "SOL", "AAVE", "XRP"]
ANN = np.sqrt(365)


def load(coin, refresh=False):
    f = f"data/{coin}USDT_D.pkl"
    if refresh:
        df = exchange.fetch_klines(coin + "USDT", "D", "spot", start_ms=int(time.time() * 1000) - 2600 * 86_400_000)
        df.to_pickle(f)
    df = pd.read_pickle(f)
    return df[df.index < pd.Timestamp.now(tz="UTC").floor("D")]["close"]      # 只用已收盤的日線


def closes(refresh=False):
    return pd.DataFrame({c: load(c, refresh) for c in COINS}).dropna()


# ---------- 訊號積木 (在 bot/strategies/dynamic.py) ----------
def deadband(W, thr=0.05):
    """權重變動小於 thr 就不動,壓低換手。歸零一律執行。"""
    out, cur = W.to_numpy().copy(), np.zeros(W.shape[1])
    for i in range(len(out)):
        tgt = np.nan_to_num(out[i])
        move = (np.abs(tgt - cur) >= thr) | ((tgt == 0) & (cur != 0))
        cur = np.where(move, tgt, cur)
        out[i] = cur
    return pd.DataFrame(out, index=W.index, columns=W.columns)


def cap_total(W, cap=1.0):
    s = W.sum(axis=1)
    return W.mul((cap / s).clip(upper=1.0).fillna(0.0), axis=0)


# ---------- 策略 (回傳每日目標權重表) ----------
def s_hold(C, w):
    return pd.DataFrame({c: pd.Series(x, index=C.index) for c, x in w.items()})


def s_ma(C, w, n=200):
    return pd.DataFrame({c: ma_on(C[c], n) * x for c, x in w.items()})


def s_ma_vt(C, w, n=200, tv=0.40, vn=30):
    return deadband(pd.DataFrame({c: ma_on(C[c], n) * vol_scale(C[c], tv, vn) * x for c, x in w.items()}))


def s_ens(C, w, **kw):
    return deadband(pd.DataFrame({c: ensemble(C[c], **kw) * x for c, x in w.items()}))


def s_ens_vt(C, w, tv=0.40, vn=30, **kw):
    return deadband(pd.DataFrame({c: ensemble(C[c], **kw) * vol_scale(C[c], tv, vn) * x for c, x in w.items()}))


def s_rotation(C, top=2, moms=(30, 90, 180), ma=100, gate=200, every=7):
    """雙動能輪動:每 every 天重排;分數=多週期報酬平均;只挑分數>0 且站上均線者的前 top 名,
    等權各 1/top (缺額放現金);BTC 跌破 gate 日均線時全數空手 (每日檢查)。"""
    score = sum(C.pct_change(n) for n in moms) / len(moms)
    ok = (score > 0) & (C > C.rolling(ma).mean())
    rank = score.where(ok).rank(axis=1, ascending=False)
    W = (rank <= top).astype(float) / top
    reb = np.arange(len(C)) % every == 0
    W = W.where(pd.Series(reb, index=C.index), axis=0).ffill().fillna(0.0)
    return W.mul(ma_on(C["BTC"], gate).fillna(0.0), axis=0)


def s_basket(C, risk=0.08, each_cap=0.35, vn=30, **kw):
    """一籃子風險平價趨勢:每檔 = 趨勢分數 × (單檔風險預算/波動),單檔上限、總和上限 1。"""
    W = pd.DataFrame({c: (ensemble(C[c], **kw) * risk / vol(C[c], vn)).clip(upper=each_cap) for c in C.columns})
    return deadband(cap_total(W), 0.03)


# ---------- 績效 ----------
def run(C, W, cost=COST):
    R = C.pct_change().fillna(0.0)
    P = W.reindex(columns=C.columns).fillna(0.0).shift().fillna(0.0)
    turn = P.diff().abs().sum(axis=1).fillna(0.0)
    return (P * R).sum(axis=1) - turn * cost, P.sum(axis=1), turn


def stats(r, expo=None, turn=None):
    eq = (1 + r).cumprod(); yrs = len(r) / 365
    dd = (eq / eq.cummax() - 1).min(); cagr = eq.iloc[-1] ** (1 / yrs) - 1
    m = (1 + r).groupby([r.index.year, r.index.month]).prod() - 1
    d = dict(總報酬=eq.iloc[-1] - 1, 年化=cagr, 最大回撤=dd, 夏普=r.mean() / r.std() * ANN if r.std() > 0 else 0.0,
             卡瑪=cagr / abs(dd) if dd else np.nan, 最差月=m.min())
    if expo is not None:
        d["平均曝險"] = expo.mean(); d["年換手"] = turn.sum() / yrs
    return d


FMT = {k: (lambda v: f"{v:+.1%}") for k in ["總報酬", "年化", "最大回撤", "最差月"]} | {
    "夏普": lambda v: f"{v:.2f}", "卡瑪": lambda v: f"{v:.2f}", "平均曝險": lambda v: f"{v:.0%}", "年換手": lambda v: f"{v:.1f}x"}

BE = {"BTC": 0.7, "ETH": 0.3}


def book(C):
    return {
        "基準 BTC 買進持有": s_hold(C, {"BTC": 1}),
        "基準 BTC 200日均線": s_ma(C, {"BTC": 1}),
        "基準 現行(BTC70/ETH30 均線)": s_ma(C, BE),
        "A BTC 均線+波動目標40%": s_ma_vt(C, {"BTC": 1}),
        "B BTC 多週期趨勢分數": s_ens(C, {"BTC": 1}),
        "C BTC 趨勢分數+波動目標": s_ens_vt(C, {"BTC": 1}),
        "D BTC70/ETH30 趨勢分數+波動目標": s_ens_vt(C, BE),
        "E 雙動能輪動(6幣取前2)": s_rotation(C),
        "F 雙動能輪動(僅BTC/ETH取1)": s_rotation(C[["BTC", "ETH"]], top=1),
        "G 一籃子風險平價趨勢(6幣)": s_basket(C),
    }


def table(C, lo, hi, cost=COST):
    rows = {}
    for k, W in book(C).items():
        r, e, t = run(C, W, cost)
        s = slice(lo, hi)
        rows[k] = stats(r.loc[s], e.loc[s], t.loc[s])
    return pd.DataFrame(rows).T


def main():
    C = closes("refresh" in sys.argv)
    end = C.index[-1]
    mid = end - pd.Timedelta(days=729)
    first = C.index[0] + pd.Timedelta(days=200)
    print(f"資料 {C.index[0].date()} ~ {end.date()},成本每次換手 {COST:.2%}\n")
    print(f"=== 主視窗:最近兩年 {mid.date()} ~ {end.date()} ===")
    print(table(C, mid, end).to_string(formatters=FMT))
    print(f"\n=== 對照:前一段 {first.date()} ~ {(mid - pd.Timedelta(days=1)).date()} (含 2022 熊市) ===")
    print(table(C, first, mid - pd.Timedelta(days=1)).to_string(formatters=FMT))
    print("\n=== 成本加倍 (0.30%/次) 的最近兩年 ===")
    print(table(C, mid, end, COST * 2)[["年化", "最大回撤", "卡瑪"]].to_string(formatters=FMT))

    print("\n=== 參數穩健性 (最近兩年 / 前一段):年化、最大回撤 ===")
    def line(name, W):
        r, _, _ = run(C, W)
        a, b = stats(r.loc[mid:end]), stats(r.loc[first:mid - pd.Timedelta(days=1)])
        print(f"  {name:38s} 近兩年 {a['年化']:+6.1%} / {a['最大回撤']:6.1%} | 前段 {b['年化']:+6.1%} / {b['最大回撤']:6.1%}")
    for tv in (0.3, 0.4, 0.5, 0.6):
        for vn in (20, 30, 60):
            line(f"C 波動目標 {tv:.0%}, 波動窗 {vn}", s_ens_vt(C, {"BTC": 1}, tv=tv, vn=vn))
    for mas, moms in [((50, 100, 200), (30, 90, 180)), ((100, 200), (90, 180)), ((50, 100, 150, 200), ()), ((), (30, 60, 90, 180)),
                      ((20, 50, 100, 200), (20, 60, 120))]:
        line(f"C 均線{mas} 動能{moms}", s_ens_vt(C, {"BTC": 1}, mas=mas, moms=moms))
    for top in (1, 2, 3):
        for every in (1, 7, 14, 30):
            line(f"E 輪動 取前{top}, 每{every}天", s_rotation(C, top=top, every=every))
    for risk in (0.05, 0.08, 0.12):
        for cap in (0.25, 0.35, 0.5):
            line(f"G 籃子 單檔風險{risk:.0%}, 上限{cap:.0%}", s_basket(C, risk=risk, each_cap=cap))

    print("\n=== 各年度報酬 ===")
    yr = {}
    for k, W in book(C).items():
        r, _, _ = run(C, W)
        r = r.loc[first:]
        yr[k] = (1 + r).groupby(r.index.year).prod() - 1
    print(pd.DataFrame(yr).T.to_string(float_format=lambda v: f"{v:+.0%}"))


if __name__ == "__main__":
    main()
