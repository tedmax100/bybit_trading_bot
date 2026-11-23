"""資金費率套利回測:現貨做多 + 永續做空 (delta-neutral),賺 funding。
funding>0 時空頭收費。忽略基差 (basis) 的盯市波動,但計入進出場雙腿手續費。"""
import numpy as np
import pandas as pd
from pybit.unified_trading import HTTP


def fetch_funding(symbol: str, days: int = 1825, c: HTTP | None = None) -> pd.Series:
    c = c or HTTP(testnet=False)
    end = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
    start, rows = end - days * 86_400_000, []
    while end > start:
        r = c.get_funding_rate_history(category="linear", symbol=symbol, startTime=start, endTime=end, limit=200)["result"]["list"]
        if not r:
            break
        rows += r
        end = int(r[-1]["fundingRateTimestamp"]) - 1
    s = pd.Series({pd.to_datetime(int(x["fundingRateTimestamp"]), unit="ms", utc=True): float(x["fundingRate"]) for x in rows})
    return s.sort_index()


def run(funding: pd.Series, mode: str = "always", enter_thr: float = 0.0001, lookback: int = 3,
        spot_fee: float = 0.001, perp_fee: float = 0.00055, slip: float = 0.0003, perp_lev: int = 2) -> dict:
    avg = funding.rolling(lookback).mean().shift()          # 只用已知資訊決定下一期是否持倉
    if mode == "always":
        pos = pd.Series(1.0, index=funding.index)
    else:
        pos, on = [], False
        for t in funding.index:
            a = avg.loc[t]
            if not on and a == a and a > enter_thr:
                on = True
            elif on and a == a and a <= 0:
                on = False
            pos.append(1.0 if on else 0.0)
        pos = pd.Series(pos, index=funding.index)
    turn = pos.diff().abs().fillna(pos.abs())
    rt_cost = 2 * (spot_fee + perp_fee + 2 * slip) / 2       # 單邊進或出:雙腿手續費+滑價
    pnl = pos * funding - turn * rt_cost                     # 以名目本金計
    capital = 1 + 1 / perp_lev                               # 現貨全額 + 永續保證金
    r = pnl / capital
    eq = (1 + r).cumprod()
    years = (funding.index[-1] - funding.index[0]).days / 365
    return {"mode": mode, "years": round(years, 2),
            "total_return": float(eq.iloc[-1] - 1),
            "annualized": float(eq.iloc[-1] ** (1 / years) - 1),
            "max_drawdown": float((eq / eq.cummax() - 1).min()),
            "time_in_market": float(pos.mean()), "switches": int((turn > 0).sum()),
            "neg_funding_periods": float((funding < 0).mean())}
