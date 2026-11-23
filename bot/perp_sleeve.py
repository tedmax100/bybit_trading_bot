"""永續合約短線部位 (小部位、多空都做、每 4 小時決策一次)。與現貨策略完全分開記帳。

規則 (回測見 README「永續短線」):
- 情境濾網:沿用現貨的趨勢分數 (站上 50/100/200 日均線、30/90/180 日動能為正的比例)。
  分數 >= 5/6 才准做多,<= 1/6 才准做空;中間不開新倉。
- 進場:4 小時收盤突破前 5 天的最高 (做多) / 最低 (做空)。
- 出場:反向突破前 2 天的高低點、趨勢分數翻過 0.5、或持有滿 5 天。
- 部位:每檔固定一口最小下單量 (BTC 0.001、ETH 0.01),槓桿 2 倍。不加碼、不攤平。

冪等:以「最後一根已收盤的 4 小時 K 線」為單位,同一根只決策一次。
模擬 (PaperExec) 用標記價撮合;實盤 (LiveExec) 需 LIVE=1 且 PERP_LIVE=1 才會送單。
"""
from __future__ import annotations

import json
import os
import time
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable

import pandas as pd

from . import exchange
from .budget import DailyBudget
from .config import SETTINGS
from .strategies.dynamic import ensemble

K = 6                                  # 一天 6 根 4 小時 K 線
N_IN, N_OUT, MAX_BARS = 5 * K, 2 * K, 5 * K
LO_SCORE, HI_SCORE = 1 / 6, 5 / 6
LEV = 2
FEE, MMR = 0.00055, 0.005              # taker 手續費、維持保證金率 (模擬強平用)
STOP_PCT = 0.10                        # 實盤:掛在交易所的保護性停損 (兩次執行之間的保險)
QTY = {"BTCUSDT": 0.001, "ETHUSDT": 0.01}
if os.getenv("PERP_QTY"):              # 例: PERP_QTY=ETHUSDT:0.01 (只做 ETH 一口)
    QTY = {k: float(v) for k, v in (x.split(":") for x in os.environ["PERP_QTY"].split(","))}
ENABLED = os.getenv("PERP_ENABLED", "0") == "1"
PERP_LIVE = os.getenv("PERP_LIVE", "0") == "1"
PREFIX = "bybotp"
SIDE = {1: "多", -1: "空"}


# ---------------------------------------------------------------- 訊號
def decide(df: pd.DataFrame, cur: int, age_bars: int) -> tuple[int, str]:
    """df = 已收盤的 4 小時 K 線。cur = 目前方向 (1 多 / -1 空 / 0 空手)。回傳 (新方向, 原因)。"""
    c = df["close"]
    if len(c) < 200 * K + 1:
        return 0, "資料不足"
    s = float(ensemble(c, k=K).iloc[-1])
    if s != s:
        return 0, "資料不足"
    px = float(c.iloc[-1])
    hi, lo = float(c.iloc[-N_IN - 1:-1].max()), float(c.iloc[-N_IN - 1:-1].min())
    xh, xl = float(c.iloc[-N_OUT - 1:-1].max()), float(c.iloc[-N_OUT - 1:-1].min())
    if cur == 0:
        if s <= LO_SCORE + 1e-9 and px < lo:
            return -1, f"趨勢分數 {s:.2f},收盤 {px:g} 跌破 5 日低 {lo:g}"
        if s >= HI_SCORE - 1e-9 and px > hi:
            return 1, f"趨勢分數 {s:.2f},收盤 {px:g} 突破 5 日高 {hi:g}"
        return 0, ""
    if age_bars >= MAX_BARS:
        return 0, "持有滿 5 天"
    if cur == 1 and (px < xl or s < 0.5):
        return 0, f"收盤 {px:g} 跌破 2 日低 {xl:g}" if px < xl else f"趨勢分數降到 {s:.2f}"
    if cur == -1 and (px > xh or s > 0.5):
        return 0, f"收盤 {px:g} 突破 2 日高 {xh:g}" if px > xh else f"趨勢分數升到 {s:.2f}"
    return cur, ""


# ---------------------------------------------------------------- 執行
class PaperExec:
    """模擬:用標記價加滑價成交。"""
    live = False

    def __init__(self, mark_fn: Callable[[str], float]):
        self.mark_fn = mark_fn

    def open(self, sym, side, qty, tag):
        px = self.mark_fn(sym) * (1 + side * SETTINGS.slippage)
        return px, qty * px * FEE

    def close(self, sym, side, qty, tag):
        px = self.mark_fn(sym) * (1 - side * SETTINGS.slippage)
        return px, qty * px * FEE

    def position(self, sym):
        return None                                           # 模擬沒有交易所可對帳


class LiveExec:
    """實盤:Bybit USDT 永續市價單 (單向持倉)。每筆單帶固定 orderLinkId,重跑不會重複下單。"""
    live = True

    def __init__(self, client, wait: float = 0.5):
        self.c, self.wait = client, wait

    def _tick(self, sym) -> str:
        return self.c.get_instruments_info(category="linear", symbol=sym)["result"]["list"][0]["priceFilter"]["tickSize"]

    def _order(self, sym, buy: bool, qty, link, **kw):
        def find():
            for fn in (self.c.get_open_orders, self.c.get_order_history):
                r = fn(category="linear", symbol=sym, orderLinkId=link)["result"]["list"]
                if r:
                    return r[0]
            return None
        if find() is None:
            try:
                self.c.place_order(category="linear", symbol=sym, side="Buy" if buy else "Sell", orderType="Market",
                                   qty=str(qty), orderLinkId=link, positionIdx=0, **kw)
            except Exception as e:
                if find() is None:
                    raise RuntimeError(f"{sym} 永續下單失敗: {str(e)[:160]}") from e
        for _ in range(20):
            o = find()
            if o and o.get("orderStatus") == "Filled":
                return float(o["avgPrice"]), abs(float(o.get("cumExecFee") or 0))
            if o and o.get("orderStatus") in ("Cancelled", "Rejected", "Deactivated"):
                raise RuntimeError(f"{sym} 永續訂單未成交 ({o.get('orderStatus')}: {o.get('rejectReason', '')})")
            time.sleep(self.wait)
        raise RuntimeError(f"{sym} 永續訂單 {link} 狀態不明,請人工確認")

    def open(self, sym, side, qty, tag):
        try:
            self.c.set_leverage(category="linear", symbol=sym, buyLeverage=str(LEV), sellLeverage=str(LEV))
        except Exception as e:
            if "110043" not in str(e):                        # 110043 = 槓桿本來就是這個值
                raise
        mark = float(self.c.get_tickers(category="linear", symbol=sym)["result"]["list"][0]["markPrice"])
        tick = Decimal(self._tick(sym))
        stop = (Decimal(str(mark * (1 - side * STOP_PCT))) / tick).to_integral_value(rounding=ROUND_HALF_UP) * tick
        return self._order(sym, side == 1, qty, f"{PREFIX}-{tag}-{sym}-O", stopLoss=str(stop))

    def close(self, sym, side, qty, tag):
        return self._order(sym, side == -1, qty, f"{PREFIX}-{tag}-{sym}-C", reduceOnly=True)

    def position(self, sym) -> float:
        """交易所上的實際部位 (多為正、空為負)。"""
        tot = 0.0
        for p in self.c.get_positions(category="linear", symbol=sym)["result"]["list"]:
            sz = float(p.get("size") or 0)
            tot += sz if p.get("side") == "Buy" else -sz if p.get("side") == "Sell" else 0.0
        return tot


# ---------------------------------------------------------------- 每 4 小時的步驟
def _bars(sym: str) -> pd.DataFrame:
    df = exchange.fetch_klines(sym, "240", "spot", start_ms=int(time.time() * 1000) - 215 * 86_400_000)
    return df[df.index + pd.Timedelta(hours=4) <= pd.Timestamp.now(tz="UTC")]     # 只用已收盤的 K 線


def _mark(sym: str) -> float:
    return float(exchange.HTTP(testnet=False).get_tickers(category="linear", symbol=sym)["result"]["list"][0]["markPrice"])


def _funding(sym: str, since_ms: int) -> list[tuple[int, float]]:
    r = exchange.HTTP(testnet=False).get_funding_rate_history(category="linear", symbol=sym, startTime=since_ms,
                                                              endTime=int(time.time() * 1000), limit=200)["result"]["list"]
    return sorted((int(x["fundingRateTimestamp"]), float(x["fundingRate"])) for x in r if int(x["fundingRateTimestamp"]) > since_ms)


def _get(db, k, default):
    r = db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def _set(db, k, v):
    db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v, ensure_ascii=False)))
    db.commit()


def _new_state():
    return {"pos": {}, "realized": 0.0, "fees": 0.0, "funding": 0.0, "trades": 0, "wins": 0, "liqs": 0,
            "last_bar": None, "last_ts": None, "log": []}


def perp_step(db_path: str = SETTINGS.db_path, bars_fn: Callable = _bars, mark_fn: Callable = _mark,
              funding_fn: Callable = _funding, executor=None, force: bool = False, now_ms: int | None = None) -> dict:
    db = DailyBudget(db_path).db
    db.execute("CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT)")
    mode = _get(db, "mode", "paper")
    if executor is None:
        if mode == "live":
            if not PERP_LIVE:
                return {"skipped": True, "why": "實盤永續未啟用 (PERP_LIVE=1 才會下單)"}
            executor = LiveExec(exchange.client())
        else:
            executor = PaperExec(mark_fn)
    if executor.live != (mode == "live"):
        raise SystemExit(f"狀態檔是 {mode} 模式,永續執行器不符;請用各自的狀態檔")

    st = {**_new_state(), **_get(db, "psleeve", {})}
    dfs = {s: bars_fn(s) for s in QTY}
    bar = max(str(d.index[-1]) for d in dfs.values())
    if not force and st["last_bar"] == bar:
        return {"skipped": True, "bar": bar}
    now_ms = now_ms or int(time.time() * 1000)
    actions = []

    def book(sym, p, px, fee, why, liq=False):
        pnl = -p["margin"] if liq else p["side"] * p["qty"] * (px - p["entry"]) - fee
        total = pnl - p["fee_in"] + p["funding"]              # 這一筆的完整損益 (含進場手續費與 funding)
        st["realized"] += pnl; st["fees"] += 0.0 if liq else fee
        st["trades"] += 1; st["wins"] += total > 0; st["liqs"] += liq
        del st["pos"][sym]
        actions.append(f"永續{'爆倉' if liq else '平倉'} {sym} {SIDE[p['side']]} @{px:g} 損益 {total:+.2f} ({why})")

    for sym, qty in QTY.items():
        df, mark, p = dfs[sym], mark_fn(sym), st["pos"].get(sym)
        if p and st["last_ts"]:                               # funding:多單在費率為正時付錢
            for _, rate in funding_fn(sym, st["last_ts"]):
                amt = -p["side"] * p["qty"] * mark * rate
                p["funding"] += amt; st["funding"] += amt; st["realized"] += amt
        if p and executor.live:                               # 對帳:交易所已無部位 (保護性停損被觸發或手動平倉)
            if abs(executor.position(sym)) < p["qty"] * 0.5:
                stop = p["entry"] * (1 - p["side"] * STOP_PCT)
                book(sym, p, stop, p["qty"] * stop * FEE, "對帳:交易所已無部位,以停損價估算")
                continue                                      # 這一根不再開新倉
        if p and not executor.live:                           # 模擬強平:進場後的 K 線碰到強平價
            seg = df[df.index >= pd.Timestamp(p["opened"])]
            liq = p["entry"] * (1 - p["side"] * (1 / LEV - MMR))
            hit = len(seg) and (seg["low"].min() <= liq if p["side"] == 1 else seg["high"].max() >= liq)
            if hit:
                book(sym, p, liq, 0.0, "碰到強平價", liq=True)
                continue
        cur = p["side"] if p else 0
        age = int((df.index[-1] - pd.Timestamp(p["opened"])) / pd.Timedelta(hours=4)) if p else 0
        tgt, why = decide(df, cur, age)
        tag = df.index[-1].strftime("%Y%m%d%H")
        if p and tgt != cur:
            px, fee = executor.close(sym, cur, p["qty"], tag)
            book(sym, p, px, fee, why)
            _set(db, "psleeve", st)                             # 每筆成交後立刻存檔:後面出錯也不會漏記
        elif not p and tgt != 0:
            if executor.live and abs(executor.position(sym)) > 0:
                actions.append(f"永續略過 {sym}: 交易所已有非機器人的部位")
                continue
            px, fee = executor.open(sym, tgt, qty, tag)
            st["pos"][sym] = {"side": tgt, "qty": qty, "entry": px, "margin": qty * px / LEV, "opened": str(df.index[-1]),
                              "fee_in": fee, "funding": 0.0}
            st["realized"] -= fee; st["fees"] += fee
            actions.append(f"永續開{SIDE[tgt]} {sym} {qty:g} @{px:g} 名目 {qty * px:.2f} ({why})")
            _set(db, "psleeve", st)

    marks = {s: mark_fn(s) for s in st["pos"]}
    unreal = sum(p["side"] * p["qty"] * (marks[s] - p["entry"]) for s, p in st["pos"].items())
    st.update(last_bar=bar, last_ts=now_ms, unreal=unreal, marks=marks)
    st["log"] = (st["log"] + [f"{bar[:16]} {a}" for a in actions])[-40:]
    _set(db, "psleeve", st)
    return {"bar": bar, "actions": actions, "realized": st["realized"], "unrealized": unreal,
            "pos": {s: SIDE[p["side"]] for s, p in st["pos"].items()}}


def sleeve_equity(db, mark_fn: Callable = _mark) -> float:
    """永續短線對帳戶權益的貢獻 (報表用)。
    實盤:手續費與 funding 已經反映在錢包的 USDT 餘額裡,只加未實現損益。
    模擬:永續與現貨的現金分開記帳,所以已實現也要加。
    取不到價格時退回上次決策時記下的未實現損益。"""
    try:
        st = _get(db, "psleeve", None)
    except Exception:                                         # 還沒有 kv 表
        return 0.0
    if not st:
        return 0.0
    try:
        unreal = sum(p["side"] * p["qty"] * (mark_fn(s) - p["entry"]) for s, p in st.get("pos", {}).items())
    except Exception:
        unreal = st.get("unreal", 0.0)
    return unreal + (0.0 if _get(db, "mode", "paper") == "live" else st.get("realized", 0.0))


def summary(db) -> list[str]:
    st = _get(db, "psleeve", None)
    if st is None:
        return []
    out = ["", f"永續短線 (每 4 小時;每檔一口、{LEV} 倍) | 已平倉 {st['trades']} 筆、賺 {st['wins']} 筆、爆倉 {st['liqs']} 次",
           f"  累計已實現 {st['realized']:+.2f} USDT (手續費 {-st['fees']:.2f}、funding {st['funding']:+.2f}) | 未實現 {st.get('unreal', 0.0):+.2f}"
           f" | 最後決策 K 線 {str(st['last_bar'])[:16]}"]
    for s, p in st["pos"].items():
        m = st.get("marks", {}).get(s, p["entry"])
        out.append(f"  持倉 {s} {SIDE[p['side']]} {p['qty']:g} 進場 {p['entry']:g} 現價 {m:g} 未實現 {p['side'] * p['qty'] * (m - p['entry']):+.2f} (自 {p['opened'][:16]})")
    if not st["pos"]:
        out.append("  (目前無永續持倉)")
    out += [f"  {x}" for x in st["log"][-6:]]
    return out
