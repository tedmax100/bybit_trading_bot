from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    name = "base"

    @abstractmethod
    def positions(self, df: pd.DataFrame) -> pd.Series:
        """回傳每根 K 線收盤後的目標持倉 (0=空手, 1=滿倉做多)。
        訊號在 bar t 收盤產生,回測於 t+1 開盤成交,避免未來函數。"""
