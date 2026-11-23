"""長期均線濾網 (現貨只做多):日線收盤 > SMA(N)*(1+band) 持有,< SMA(N)*(1-band) 空手。
band 為遲滯帶,減少在均線附近來回洗。"""
import numpy as np
import pandas as pd

from .base import Strategy


class MAFilter(Strategy):
    name = "ma_filter"

    def __init__(self, n: int = 200, band: float = 0.0):
        self.n, self.band = n, band

    def positions(self, df: pd.DataFrame) -> pd.Series:
        ma, c = df["close"].rolling(self.n).mean(), df["close"].to_numpy()
        pos, on = np.zeros(len(df)), False
        for i in range(len(df)):
            if np.isnan(ma.iloc[i]):
                continue
            if not on and c[i] > ma.iloc[i] * (1 + self.band):
                on = True
            elif on and c[i] < ma.iloc[i] * (1 - self.band):
                on = False
            pos[i] = 1.0 if on else 0.0
        return pd.Series(pos, index=df.index)
