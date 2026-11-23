"""永續合約閘門:每天結算時評估,全部通過才允許開永續;否則只做現貨。"""
from dataclasses import dataclass

import pandas as pd

from .indicators import adx, atr


@dataclass
class GateResult:
    open: bool
    reasons: list[str]


def perp_gate(df: pd.DataFrame, funding: float, halted_recently: bool = False,
              adx_min: float = 25.0, atr_pct_range: tuple[float, float] = (0.005, 0.04),
              max_abs_funding: float = 0.0005) -> GateResult:
    fails = []
    a = adx(df).iloc[-1]
    ap = (atr(df).iloc[-1] / df["close"].iloc[-1])
    if a < adx_min:
        fails.append(f"ADX {a:.1f} < {adx_min} (無明確趨勢)")
    if not (atr_pct_range[0] <= ap <= atr_pct_range[1]):
        fails.append(f"ATR% {ap:.3%} 不在 {atr_pct_range} (波動過低/過高)")
    if abs(funding) > max_abs_funding:
        fails.append(f"funding {funding:.4%} 過大 (持倉成本偏高)")
    if halted_recently:
        fails.append("近期曾觸發熔斷")
    return GateResult(not fails, fails)
