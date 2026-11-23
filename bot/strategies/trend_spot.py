"""現貨趨勢跟隨 (只做多):Donchian 突破進場,ATR 追蹤停損或跌破短期低點出場。"""
import numpy as np
import pandas as pd

from ..indicators import atr
from .base import Strategy


class TrendSpot(Strategy):
    name = "trend_spot"

    def __init__(self, entry_n: int = 20, exit_n: int = 10, atr_n: int = 14, atr_mult: float = 3.0):
        self.entry_n, self.exit_n, self.atr_n, self.atr_mult = entry_n, exit_n, atr_n, atr_mult

    def positions(self, df: pd.DataFrame) -> pd.Series:
        hi = df["high"].rolling(self.entry_n).max().shift()
        lo = df["low"].rolling(self.exit_n).min().shift()
        a = atr(df, self.atr_n)
        close = df["close"].to_numpy()
        pos = np.zeros(len(df))
        in_pos, peak = False, 0.0
        for i in range(len(df)):
            if np.isnan(hi.iloc[i]) or np.isnan(lo.iloc[i]):
                continue
            if not in_pos and close[i] > hi.iloc[i]:
                in_pos, peak = True, close[i]
            elif in_pos:
                peak = max(peak, close[i])
                if close[i] < peak - self.atr_mult * a.iloc[i] or close[i] < lo.iloc[i]:
                    in_pos = False
            pos[i] = 1.0 if in_pos else 0.0
        return pd.Series(pos, index=df.index)
