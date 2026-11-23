"""槓桿永續網格回測 (只做多)。邏輯同 grid.py,差異:
- 每格名目 = lev * 權益 / n,現金可為負 (=保證金借款)。
- 每根 K 線低點檢查強平:權益 <= 維持保證金(名目*mm) -> 以低點強平,再扣強平費,剩餘權益留到下次重設。
- 每 8 小時依 funding 收付 (多頭付正費率)。
- 掛單 maker 費率 maker,市價單 taker+滑價。價格以現貨 1H 近似永續。"""
import numpy as np
import pandas as pd

MAKER, TAKER, SLIP, MM, LIQ_FEE = 0.0002, 0.00055, 0.0005, 0.005, 0.005


def run_grid_lev(df, funding, w, n, stop, reset_bars, lev=1.0):
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    N = len(df)
    fmap = funding.reindex(df.index).fillna(0.0).to_numpy()   # 只在整點對上結算時間的 bar 有值
    eq = np.empty(N)
    cash = 1.0
    levels = held = qty = None
    active, next_reset, nstops, nliq = False, 0, 0, 0
    wait_reset = False

    def flatten(price):
        nonlocal cash, held, active
        if held is not None and held.any():
            cash += float((qty * held).sum()) * price * (1 - SLIP) * (1 - TAKER)
        held = None; active = False

    for t in range(N):
        if t >= next_reset:
            if active or held is not None:
                flatten(c[t - 1] if t else o[t])
            P = o[t]
            if cash > 0:
                levels = np.linspace(P * (1 - w), P * (1 + w), n + 1)
                qty = (lev * cash / n) / levels[:-1]
                held = levels[:-1] >= P
                cash -= float((qty * held).sum()) * P * (1 + SLIP) * (1 + TAKER)
                active = True
            next_reset = t + reset_bars
        if active:
            if fmap[t] != 0.0:                                   # funding
                cash -= float((qty * held).sum()) * c[t] * fmap[t]
            up = c[t] >= o[t]
            seq = (("buy", l[t]), ("sell", h[t])) if up else (("sell", h[t]), ("buy", l[t]))
            for kind, px in seq:
                if kind == "buy":
                    idx = np.where((~held) & (levels[:-1] >= px))[0]
                    idx = idx[levels[:-1][idx] <= max(o[t], c[t])] if len(idx) else idx
                    for i in idx:
                        cash -= qty[i] * levels[i] * (1 + MAKER); held[i] = True
                else:
                    idx = np.where(held & (levels[1:] <= px))[0]
                    for i in idx:
                        cash += qty[i] * levels[i + 1] * (1 - MAKER); held[i] = False
                # 強平檢查 (買單成交後在低點最不利)
                coin = float((qty * held).sum())
                pxc = l[t] if kind == "buy" else h[t]
                if coin > 0 and kind == "buy":
                    if cash + coin * pxc <= coin * pxc * MM:
                        cash = max(0.0, cash + coin * pxc - coin * pxc * LIQ_FEE)
                        held = None; active = False; nliq += 1
                        break
            if active and c[t] < levels[0] * (1 - stop):
                flatten(c[t]); nstops += 1
        coin = float((qty * held).sum()) if held is not None else 0.0
        eq[t] = cash + coin * c[t]
    out = pd.Series(eq, index=df.index)
    out.attrs.update(stops=nstops, liqs=nliq)
    return out
