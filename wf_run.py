import sys, time
from pathlib import Path
import pandas as pd
from bot import exchange
from bot.walkforward import walk_forward, robustness

for sym in sys.argv[1:] or ["BTCUSDT", "ETHUSDT"]:
    f = Path(f"data/{sym}_4h.pkl")
    if f.exists():
        df = pd.read_pickle(f)
    else:
        end = int(time.time() * 1000)
        df = exchange.fetch_klines(sym, "240", "spot", start_ms=end - 1825 * 86_400_000)
        f.parent.mkdir(exist_ok=True); df.to_pickle(f)
    oos, picks = walk_forward(df)
    eq = (1 + oos).cumprod()
    print(f"\n== {sym} {df.index[0].date()}~{df.index[-1].date()} bars={len(df)}")
    print(f"樣本外總報酬 {eq.iloc[-1]-1:+.1%}  最大回撤 {(eq/eq.cummax()-1).min():.1%}  同期買進持有 {df['close'].iloc[-1]/df['close'].loc[oos.index[0]]-1:+.1%}")
    for d, p, r in picks: print(f"  {d} 選 {p} -> 樣本外 {r:+.1%}")
    print("全樣本參數穩健性:", robustness(df))
