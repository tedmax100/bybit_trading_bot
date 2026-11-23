"""現貨區間均值回歸 (只做多):盤整(ADX低)時,收盤跌破布林下軌買進,回到中軌賣出。
停損:跌破進場價 - stop_atr*ATR;時間停損:持有超過 max_bars 根即出場。"""
import numpy as np
import pandas as pd

from ..indicators import adx, atr, bollinger
from .base import Strategy


class MeanRevSpot(Strategy):
    name = "meanrev_spot"

    def __init__(self, bb_n=20, bb_k=2.0, adx_max=25.0, stop_atr=3.0, max_bars=48):
        self.bb_n, self.bb_k, self.adx_max, self.stop_atr, self.max_bars = bb_n, bb_k, adx_max, stop_atr, max_bars

    def positions(self, df: pd.DataFrame) -> pd.Series:
        lo, mid, _ = bollinger(df["close"], self.bb_n, self.bb_k)
        a, ax = atr(df), adx(df)
        c = df["close"].to_numpy()
        pos = np.zeros(len(df))
        in_pos, stop, held = False, 0.0, 0
        for i in range(len(df)):
            if np.isnan(lo.iloc[i]) or np.isnan(ax.iloc[i]):
                continue
            if not in_pos:
                if c[i] < lo.iloc[i] and ax.iloc[i] < self.adx_max:
                    in_pos, stop, held = True, c[i] - self.stop_atr * a.iloc[i], 0
            else:
                held += 1
                if c[i] >= mid.iloc[i] or c[i] < stop or held >= self.max_bars:
                    in_pos = False
            pos[i] = 1.0 if in_pos else 0.0
        return pd.Series(pos, index=df.index)
