import sys, time
import pandas as pd
from pybit.unified_trading import HTTP
c = HTTP(testnet=False)
STEP = 300_000 * 1000
end0 = int(time.time() * 1000)
for sym in sys.argv[1:]:
    start, end, rows = end0 - 1825 * 86_400_000, end0, []
    while end > start:
        for k in range(8):
            try:
                r = c.get_kline(category="spot", symbol=sym, interval="5", start=max(start, end - STEP), end=end, limit=1000)["result"]["list"]
                break
            except Exception as e:
                print("retry", k, str(e)[:60], flush=True); time.sleep(2 * (k + 1))
        else:
            raise SystemExit("放棄")
        if not r: break
        rows += r; end = int(r[-1][0]) - 1
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"]).astype(
        {"ts": "int64", **{k: "float64" for k in ["open", "high", "low", "close", "volume", "turnover"]}})
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.drop_duplicates("ts").sort_values("ts").set_index("ts")
    df.to_pickle(f"data/{sym}_5m.pkl"); print(sym, len(df), flush=True)
