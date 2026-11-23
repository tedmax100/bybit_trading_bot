import sys, time
import pandas as pd
from bot import exchange
end = int(time.time() * 1000)
for sym in sys.argv[1:]:
    df = exchange.fetch_klines(sym, "60", "spot", start_ms=end - 1825 * 86_400_000)
    df.to_pickle(f"data/{sym}_60.pkl"); print(sym, len(df), df.index[0].date())
