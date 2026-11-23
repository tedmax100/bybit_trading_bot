import pandas as pd
from pathlib import Path
from bot.funding_arb import fetch_funding, run

for sym in ["BTCUSDT", "ETHUSDT"]:
    f = Path(f"data/{sym}_funding.pkl")
    s = pd.read_pickle(f) if f.exists() else fetch_funding(sym)
    if not f.exists(): s.to_pickle(f)
    print(f"\n== {sym} {s.index[0].date()}~{s.index[-1].date()} n={len(s)} 平均 funding/期 {s.mean():.5%}")
    for yr, g in s.groupby(s.index.year):
        print(f"   {yr}: 年化(單純加總) {g.sum()*365/ (len(g)/ (len(g)/365 if len(g)>0 else 1)) if False else g.mean()*3*365:+.2%}")
    for kw in [dict(mode="always"), dict(mode="filter"), dict(mode="filter", enter_thr=0.0002)]:
        r = run(s, **kw)
        print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}, kw.get("enter_thr", ""))
