import numpy as np, pandas as pd
from bot.config import SETTINGS
from bot.strategies.ma_filter import MAFilter

COST = SETTINGS.spot_fee + SETTINGS.slippage
CAP = 1.5                         # 套利佔用資金 = 現貨全額 + 2 倍槓桿永續保證金
FA_ENTRY = 2 * (0.001 + 0.00055 + 2 * 0.0003)

def ma_ret(coin):
    df = pd.read_pickle(f"data/{coin}USDT_D.pkl")
    pos = MAFilter(200).positions(df).shift().fillna(0.0)
    turn = pos.diff().abs().fillna(pos.abs())
    return pos * df["close"].pct_change().fillna(0.0) - turn * COST, df["close"].pct_change().fillna(0.0)

def fa_ret(coin, idx):
    s = pd.read_pickle(f"data/{coin}USDT_funding.pkl")
    d = s.groupby(s.index.floor("D")).sum().reindex(idx).fillna(0.0) / CAP
    d.iloc[d.to_numpy().nonzero()[0][0]] -= FA_ENTRY / CAP          # 第一天一次性進場成本
    return d

btc_ma, btc_bh = ma_ret("BTC"); eth_ma, eth_bh = ma_ret("ETH")
idx = btc_ma.index.intersection(eth_ma.index)
start = max(pd.Timestamp("2021-11-10", tz="UTC"), idx[0] + pd.Timedelta(days=200))
idx = idx[idx >= start]
R = pd.DataFrame({"btc_ma": btc_ma, "eth_ma": eth_ma, "btc_bh": btc_bh, "eth_bh": eth_bh}).loc[idx]
R["fa"] = 0.5 * fa_ret("BTC", idx) + 0.5 * fa_ret("ETH", idx)
yrs = len(R) / 365
print(f"比較期間 {idx[0].date()} ~ {idx[-1].date()} ({yrs:.1f} 年)\n相關係數:\n{R.corr().round(2).to_string()}\n")

def st(r):
    eq = (1 + r).cumprod(); dd = (eq / eq.cummax() - 1).min(); cagr = eq.iloc[-1] ** (1 / yrs) - 1
    yr = (1 + r).groupby(r.index.year).prod() - 1
    return dict(總報酬=eq.iloc[-1] - 1, 年化=cagr, 最大回撤=dd, 夏普=r.mean() / r.std() * np.sqrt(365), 最差年=yr.min(), 卡瑪=cagr / abs(dd) if dd else np.nan)

mixes = {
    "BTC 買進持有": dict(btc_bh=1),
    "BTC 均線 100%": dict(btc_ma=1),
    "BTC+ETH 均線 70/30": dict(btc_ma=.7, eth_ma=.3),
    "只做資金費率套利": dict(fa=1),
    "均線(BTC70/ETH30) 70% + 套利 30%": dict(btc_ma=.49, eth_ma=.21, fa=.30),
    "均線(BTC70/ETH30) 50% + 套利 50%": dict(btc_ma=.35, eth_ma=.15, fa=.50),
    "BTC均線 40% + 套利 60%": dict(btc_ma=.4, fa=.6),
}
rows = {}
for k, w in mixes.items():
    r = sum(R[c] * x for c, x in w.items()); rows[k] = st(r)
T = pd.DataFrame(rows).T
f = {c: (lambda v: f"{v:+.1%}") for c in ["總報酬", "年化", "最大回撤", "最差年"]} | {"夏普": lambda v: f"{v:.2f}", "卡瑪": lambda v: f"{v:.2f}"}
print(T.to_string(formatters=f))

main = sum(R[c] * x for c, x in mixes["均線(BTC70/ETH30) 70% + 套利 30%"].items())
h = len(main) // 2
print("\n主組合 前後半段:")
for nm, r, b in [("前半", main.iloc[:h], R.btc_bh.iloc[:h]), ("後半", main.iloc[h:], R.btc_bh.iloc[h:])]:
    s, sb = st(r), st(b)
    print(f"  {nm}: 組合 報酬 {s['總報酬']:+.0%} 回撤 {s['最大回撤']:.0%} | BTC 持有 報酬 {sb['總報酬']:+.0%} 回撤 {sb['最大回撤']:.0%}")
acct = st(main)
print(f"\n換算總權益 (只動用 5%): 年化約 {acct['年化']*0.05:+.2%},最大回撤約 {acct['最大回撤']*0.05:.2%}")
