import sys, time
import pandas as pd
from bot import exchange
end = int(time.time() * 1000)
for sym in sys.argv[1:]:
    df = exchange.fetch_klines(sym, "5", "spot", start_ms=end - 1825 * 86_400_000)
    d = df.resample("10min").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                  "volume": "sum", "turnover": "sum"}).dropna()
    d.to_pickle(f"data/{sym}_10m.pkl"); print(sym, len(df), "->", len(d), d.index[0].date())
