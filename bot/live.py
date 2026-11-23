"""實盤現貨下單 (Bybit v5)。只做現貨市價單,不碰合約、不提現。

- 持倉只算「機器人自己買的」(存在狀態檔),帳戶裡原有的零頭或你手動買的幣不會被賣掉。
- 每筆單帶固定的 orderLinkId (bybot-日期-幣-買賣):同一天重跑不會重複下單,而是認領已成交的那一筆。
- reconcile():狀態檔沒存到的成交 (例如下單後程式掛掉) 會從交易所的訂單紀錄補回來。
"""
from __future__ import annotations

import time
from decimal import ROUND_DOWN, Decimal

from .broker import Broker, Fill, OrderRejected

PREFIX = "bybot"
DONE = {"Filled", "PartiallyFilledCanceled", "Cancelled", "Rejected", "Deactivated"}


def _floor(x: float, step: str) -> Decimal:
    s = Decimal(step)
    return (Decimal(str(x)) / s).to_integral_value(rounding=ROUND_DOWN) * s


class BybitBroker(Broker):
    def __init__(self, client, day: str, hold: dict | None = None, orders: list | None = None, wait: float = 0.5):
        self.c, self.day, self.wait = client, day, wait
        self._hold: dict[str, float] = dict(hold or {})
        self.orders: list[str] = list(orders or [])          # 已計入狀態的 orderId
        self._lot: dict[str, dict] = {}

    # ---- 查詢 ----
    def price(self, symbol):
        return float(self.c.get_tickers(category="spot", symbol=symbol)["result"]["list"][0]["lastPrice"])

    def holdings(self):
        return {k: v for k, v in self._hold.items() if v > 1e-12}

    def balance(self, coin: str) -> float:
        for x in self.c.get_wallet_balance(accountType="UNIFIED")["result"]["list"][0]["coin"]:
            if x["coin"] == coin:
                return float(x.get("walletBalance") or 0) - float(x.get("locked") or 0)
        return 0.0

    @property
    def cash(self) -> float:
        return self.balance("USDT")

    def lot(self, symbol) -> dict:
        if symbol not in self._lot:
            self._lot[symbol] = self.c.get_instruments_info(category="spot", symbol=symbol)["result"]["list"][0]["lotSizeFilter"]
        return self._lot[symbol]

    # ---- 下單 ----
    def _link(self, symbol, side) -> str:
        return f"{PREFIX}-{self.day}-{symbol}-{side[0]}"

    def _find(self, symbol, link) -> dict | None:
        for fn in (self.c.get_open_orders, self.c.get_order_history):
            r = fn(category="spot", symbol=symbol, orderLinkId=link)["result"]["list"]
            if r:
                return r[0]
        return None

    def _fill(self, o: dict) -> Fill:
        """把一筆已結束的訂單換算成 Fill:買單的手續費從拿到的幣裡扣,qty 為實得數量;fee 一律換算成 USDT。"""
        symbol, base = o["symbol"], o["symbol"][:-4]
        qty, val = float(o.get("cumExecQty") or 0), float(o.get("cumExecValue") or 0)
        if qty <= 0:
            raise OrderRejected(f"{symbol} 訂單未成交 ({o.get('orderStatus')}: {o.get('rejectReason', '')})")
        px, fee_usdt, fee_base = val / qty, 0.0, 0.0
        for _ in range(10):                                   # 成交明細可能比訂單狀態晚一點出現
            ex = self.c.get_executions(category="spot", symbol=symbol, orderId=o["orderId"])["result"]["list"]
            if ex and sum(float(e["execQty"]) for e in ex) >= qty * (1 - 1e-9):
                break
            time.sleep(self.wait)
        else:
            raise RuntimeError(f"{symbol} 訂單 {o['orderId']} 已成交但查不到成交明細,請人工確認")
        for e in ex:
            f = float(e.get("execFee") or 0)
            if e.get("feeCurrency") == base:
                fee_base += f; fee_usdt += f * float(e["execPrice"])
            else:
                fee_usdt += f
        net = qty - fee_base if o["side"] == "Buy" else qty
        return Fill(symbol, o["side"], net, px, fee_usdt)

    def _apply(self, o: dict) -> Fill:
        f = self._fill(o)
        sign = 1 if f.side == "Buy" else -1
        self._hold[f.symbol] = max(0.0, self._hold.get(f.symbol, 0.0) + sign * f.qty)
        self.orders = (self.orders + [o["orderId"]])[-200:]
        return f

    def _market(self, symbol, side, qty: str, **kw) -> Fill:
        link = self._link(symbol, side)
        o = self._find(symbol, link)
        if o and o["orderId"] in self.orders:
            raise OrderRejected(f"{symbol} 今天已{'買' if side == 'Buy' else '賣'}過一次 (每天每個方向只下一筆)")
        if o is None:
            try:
                self.c.place_order(category="spot", symbol=symbol, side=side, orderType="Market", qty=qty, orderLinkId=link, **kw)
            except Exception as e:
                if self._find(symbol, link) is None:           # 確定沒送出才算拒絕
                    raise OrderRejected(f"{symbol} 下單失敗: {str(e)[:120]}") from e
        for _ in range(20):
            o = self._find(symbol, link)
            if o and o.get("orderStatus") in DONE:
                return self._apply(o)
            time.sleep(self.wait)
        raise RuntimeError(f"{symbol} 訂單 {link} 狀態不明,請人工確認")

    def buy_quote(self, symbol, quote_usdt):
        amt = _floor(quote_usdt, "0.01")
        if amt < Decimal(self.lot(symbol)["minOrderAmt"]):
            raise OrderRejected(f"{symbol} 下單額 {amt} 低於交易所最小金額 {self.lot(symbol)['minOrderAmt']}")
        return self._market(symbol, "Buy", str(amt), marketUnit="quoteCoin")

    def sell_qty(self, symbol, qty):
        lot, px = self.lot(symbol), self.price(symbol)
        held = min(self._hold.get(symbol, 0.0), self.balance(symbol[:-4]))
        qty = min(qty, held)
        if (held - qty) * px < float(lot["minOrderAmt"]) * 1.1:   # 剩下的零頭之後賣不掉,乾脆一次賣完
            qty = held
        q = _floor(qty, lot["basePrecision"])
        if q < Decimal(lot["minOrderQty"]) or float(q) * px < float(lot["minOrderAmt"]):
            raise OrderRejected(f"{symbol} 持有 {held:.8f} 低於交易所最小下單量,無法賣出")
        f = self._market(symbol, "Sell", str(q))
        if self._hold.get(symbol, 0.0) < float(lot["basePrecision"]):
            self._hold.pop(symbol, None)                       # 不足一個最小單位的殘量視為 0
        return f

    # ---- 對帳 ----
    def reconcile(self, cost: dict, symbols) -> list[str]:
        """補回狀態檔沒記到的成交,並把持倉壓到不超過帳戶實際餘額。回傳說明文字 (給 actions 用)。"""
        notes = []
        for sym in symbols:
            for o in self.c.get_order_history(category="spot", symbol=sym, limit=50)["result"]["list"]:
                if not o.get("orderLinkId", "").startswith(PREFIX + "-") or o["orderId"] in self.orders:
                    continue
                if o.get("orderStatus") not in DONE or float(o.get("cumExecQty") or 0) <= 0:
                    continue
                before = self._hold.get(sym, 0.0)
                f = self._apply(o)
                if f.side == "Buy":
                    cost[sym] = cost.get(sym, 0.0) + float(o["cumExecValue"])
                else:
                    cost[sym] = cost.get(sym, 0.0) * (self._hold.get(sym, 0.0) / before if before else 0.0)
                notes.append(f"補登 {sym} {f.side} {f.qty:.8f} (訂單 {o['orderLinkId']})")
            have, bal = self._hold.get(sym, 0.0), self.balance(sym[:-4])
            if have > bal + 1e-12:
                cost[sym] = cost.get(sym, 0.0) * (bal / have)
                self._hold[sym] = bal
                notes.append(f"對帳 {sym}: 帳戶餘額 {bal:.8f} 少於紀錄 {have:.8f},以帳戶為準")
        return notes
