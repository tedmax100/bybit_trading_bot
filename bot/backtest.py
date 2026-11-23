import numpy as np
import pandas as pd

from .config import SETTINGS
from .strategies.base import Strategy


def backtest(df: pd.DataFrame, strat: Strategy, fee: float = SETTINGS.spot_fee,
             slip: float = SETTINGS.slippage, budget_pct: float = SETTINGS.budget_pct) -> dict:
    pos = strat.positions(df).shift().fillna(0.0)          # t 收盤訊號 -> t+1 持倉
    ret = df["close"].pct_change().fillna(0.0)
    turnover = pos.diff().abs().fillna(pos.abs())
    strat_ret = pos * ret - turnover * (fee + slip)         # 每次進出各付手續費+滑價
    eq = (1 + strat_ret).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    bars_per_year = pd.Timedelta(days=365) / (df.index[1] - df.index[0])
    sharpe = strat_ret.mean() / strat_ret.std() * np.sqrt(bars_per_year) if strat_ret.std() > 0 else 0.0
    trades = int((turnover > 0).sum() // 2)
    bh = df["close"].iloc[-1] / df["close"].iloc[0] - 1
    return {
        "strategy": strat.name, "bars": len(df), "trades": trades,
        "return_on_deployed": float(eq.iloc[-1] - 1),       # 動用資金的報酬
        "return_on_account": float((eq.iloc[-1] - 1) * budget_pct),  # 換算成總權益 (只動用 5%)
        "max_drawdown": float(dd), "sharpe": float(sharpe), "buy_hold": float(bh),
        "exposure": float(pos.mean()),
    }
