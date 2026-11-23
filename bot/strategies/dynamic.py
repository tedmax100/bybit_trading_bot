"""動態部位的訊號積木 (日線收盤價序列 -> 0~1 的目標曝險)。回測 (dyn_run.py) 與觀察模式 (runner.py) 共用。"""
import numpy as np
import pandas as pd

ANN = np.sqrt(365)


def ma_on(c: pd.Series, n: int = 200) -> pd.Series:
    ma = c.rolling(n).mean()
    return (c > ma).astype(float).where(ma.notna())


def ensemble(c: pd.Series, mas=(50, 100, 200), moms=(30, 90, 180), ema_short: bool = False, k: int = 1) -> pd.Series:
    """多週期趨勢分數 0~1:站上幾條均線、幾個週期動能為正,取平均 -> 分批進出而非一刀切。
    k: 一天幾根 K 線 (日線 k=1, 4小時線 k=6)。
    ema_short: 若為 True,加入 4H/短期 EMA21 > EMA50 順勢排列濾網。
    """
    parts = [ma_on(c, n * k) for n in mas] + [(c.pct_change(n * k) > 0).astype(float).where(c.shift(n * k).notna()) for n in moms]
    if ema_short:
        span_fast = 21 if k > 1 else max(3, 21 // 6)
        span_slow = 50 if k > 1 else max(8, 50 // 6)
        ema_f = c.ewm(span=span_fast, adjust=False).mean()
        ema_s = c.ewm(span=span_slow, adjust=False).mean()
        parts.append(((c > ema_f) & (ema_f > ema_s)).astype(float))
    return sum(parts) / len(parts)


def vol(c: pd.Series, n: int = 30, ppy: float = 365) -> pd.Series:
    """年化波動;ppy = 一年有幾根 K 線 (日線 365、4 小時線 2190)。"""
    return c.pct_change().rolling(n).std() * np.sqrt(ppy)


def vol_scale(c: pd.Series, tv: float = 0.40, n: int = 30, ppy: float = 365) -> pd.Series:
    """波動目標:波動越大部位越小,上限 1 (不加槓桿)。"""
    return (tv / vol(c, n, ppy)).clip(upper=1.0)


def trend_vol(c: pd.Series, tv: float = 0.40, n: int = 30, ppy: float = 365, **kw) -> pd.Series:
    """趨勢分數 × 波動目標。"""
    return ensemble(c, **kw) * vol_scale(c, tv, n, ppy)
