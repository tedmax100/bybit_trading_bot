"""帶停損的現貨網格回測 (只做多)。
- 每 reset_days 以當時價格為中心設區間 [P*(1-w), P*(1+w)],等距切 n 格,每格資金 1/n。
- 格 i:價格跌到 L_i 買進、漲到 L_{i+1} 賣出。起始時在現價之上的格先以市價建倉。
- 收盤跌破下緣 stop 幅度 -> 全部市價出場,空手等到下次重設。
- 一根 K 線內成交順序:陽線先低後高、陰線先高後低 (偏向對網格有利,結果偏樂觀)。
- 掛單成交收 maker 費率,市價單 (建倉/停損/重設) 另加滑價。"""
import numpy as np
import pandas as pd

from .config import SETTINGS


def run_grid(df: pd.DataFrame, w: float, n: int, stop: float, reset_bars: int,
             fee: float = SETTINGS.spot_fee, slip: float = SETTINGS.slippage) -> pd.Series:
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    N = len(df)
    eq = np.empty(N)
    cash, coin_total = 1.0, 0.0
    levels = held = qty = None
    active, next_reset, nstops = False, 0, 0

    def flatten(price):
        nonlocal cash, held, active
        if held is not None and held.any():
            cash += float((qty * held).sum()) * price * (1 - slip) * (1 - fee)
        held = None; active = False

    for t in range(N):
        if t >= next_reset:                                   # 重設:先清倉再重建
            if active or held is not None:
                flatten(c[t - 1] if t else o[t])
            P = o[t]
            levels = np.linspace(P * (1 - w), P * (1 + w), n + 1)
            qty = (1.0 / n) / levels[:-1]                      # 每格幣量 (以買價計 1/n 資金)
            held = levels[:-1] >= P
            cost = float((qty * held).sum()) * P * (1 + slip) * (1 + fee)
            budget = cash                                      # 本期可用資金 = 現有現金
            scale = min(1.0, budget / (1.0 + 1e-12)) if budget > 0 else 0.0
            qty = qty * scale
            cash -= float((qty * held).sum()) * P * (1 + slip) * (1 + fee)
            active, next_reset = True, t + reset_bars
        if active:
            up = c[t] >= o[t]
            seq = (("buy", l[t]), ("sell", h[t])) if up else (("sell", h[t]), ("buy", l[t]))
            for kind, px in seq:
                if kind == "buy":                              # 低點觸及 -> 買入所有價位 >= 低點且空的格
                    idx = np.where((~held) & (levels[:-1] >= px))[0]
                    # 只有「現價在上方」的格才會被向下穿越;開盤已低於該價位的格視為已掛單可成交
                    idx = idx[levels[:-1][idx] <= max(o[t], c[t])] if len(idx) else idx
                    for i in idx:
                        spend = qty[i] * levels[i] * (1 + fee)
                        if cash >= spend - 1e-12:
                            cash -= spend; held[i] = True
                else:                                           # 高點觸及 -> 賣出所有價位 <= 高點且持有的格
                    idx = np.where(held & (levels[1:] <= px))[0]
                    for i in idx:
                        cash += qty[i] * levels[i + 1] * (1 - fee); held[i] = False
            if c[t] < levels[0] * (1 - stop):                  # 停損
                flatten(c[t]); nstops += 1
        coin = float((qty * held).sum()) if held is not None else 0.0
        eq[t] = cash + coin * c[t]
    out = pd.Series(eq, index=df.index)
    out.attrs["stops"] = nstops
    return out
