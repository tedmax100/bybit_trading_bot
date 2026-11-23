import numpy as np, pandas as pd
from portfolio_run import ma_ret          # 重用均線策略日報酬 (會順便印出舊表,忽略)
res = pd.read_pickle("data/fa_full_res.pkl")
nm = "模擬設定: 1.5x、漲30%置中"
fa = 0.5 * res[("BTC", nm)].pct_change().fillna(0) + 0.5 * res[("ETH", nm)].pct_change().fillna(0)
btc_ma, _ = ma_ret("BTC"); eth_ma, _ = ma_ret("ETH")
R = pd.DataFrame({"ma": 0.7 * btc_ma + 0.3 * eth_ma, "fa": fa}).dropna()
R = R[R.index >= pd.Timestamp("2022-01-21", tz="UTC")]
yrs = len(R) / 365
def st(r):
    e = (1 + r).cumprod(); return f"年化 {e.iloc[-1]**(1/yrs)-1:+6.1%}  回撤 {(e/e.cummax()-1).min():6.1%}  最差年 {((1+r).groupby(r.index.year).prod()-1).min():+6.1%}"
print("\n\n=== 以較真實的套利 (含基差/強平/置中) 重算組合,2022-01 ~ 2026-11 ===")
for k, w in {"只有均線(BTC70/ETH30)": (1, 0), "均線 70% + 套利 30%": (.7, .3), "均線 50% + 套利 50% (模擬現行)": (.5, .5)}.items():
    print(f"{k:30s}", st(w[0] * R.ma + w[1] * R.fa))
print("套利單獨:", st(R.fa), "| 與均線相關係數", round(R.ma.corr(R.fa), 2))
print("均線策略空手(閒置)天數占比:", f"{(R.ma == 0).mean():.0%}")
