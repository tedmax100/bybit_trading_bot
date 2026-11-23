import itertools, sys, time
from pathlib import Path
import numpy as np
import pandas as pd
from bot import exchange
from bot.config import SETTINGS
from bot.strategies.meanrev_spot import MeanRevSpot
from bot.walkforward import sharpe

GRID = list(itertools.product([20, 40], [1.5, 2.0, 2.5], [20, 30], [2.0, 3.0], [24, 48]))
FEE, SLIP = SETTINGS.spot_fee, SETTINGS.slippage

def rets(df, p):
    pos = MeanRevSpot(*p).positions(df).shift().fillna(0.0)
    turn = pos.diff().abs().fillna(pos.abs())
    return pos * df["close"].pct_change().fillna(0.0) - turn * (FEE + SLIP)

def load(sym, iv):
    f = Path(f"data/{sym}_{iv}.pkl")
    if f.exists(): return pd.read_pickle(f)
    end = int(time.time() * 1000)
    df = exchange.fetch_klines(sym, iv, "spot", start_ms=end - 1825 * 86_400_000)
    df.to_pickle(f); return df

for sym in ["BTCUSDT", "ETHUSDT"]:
    for iv, bpd in [("240", 6), ("60", 24)]:
        df = load(sym, iv)
        tr_n, te_n, warm = bpd * 365, bpd * 90, 100
        oos, picks, i = [], [], 0
        while i + tr_n + te_n <= len(df):
            tr = df.iloc[i:i + tr_n]; te = df.iloc[i + tr_n - warm:i + tr_n + te_n]
            best = max(GRID, key=lambda p: sharpe(rets(tr, p)))
            r = rets(te, best).iloc[warm:]; oos.append(r)
            picks.append(float((1 + r).prod() - 1)); i += te_n
        o = pd.concat(oos); eq = (1 + o).cumprod()
        full = [float((1 + rets(df, p)).prod() - 1) for p in GRID]
        pos = MeanRevSpot(20, 2.0, 25, 3.0, 48).positions(df)
        print(f"\n== {sym} {'4H' if iv=='240' else '1H'}  樣本外 {len(picks)} 段,賺錢 {sum(x>0 for x in picks)} 段")
        print(f"   樣本外總報酬 {eq.iloc[-1]-1:+.1%}  最大回撤 {(eq/eq.cummax()-1).min():.1%}  同期買進持有 {df['close'].iloc[-1]/df['close'].loc[o.index[0]]-1:+.1%}")
        print(f"   全樣本 {len(GRID)} 組參數:賺錢 {sum(x>0 for x in full)} 組,中位數 {np.median(full):+.1%},最好 {max(full):+.1%},最差 {min(full):+.1%}")
        print(f"   預設參數在場時間 {pos.mean():.1%}")
