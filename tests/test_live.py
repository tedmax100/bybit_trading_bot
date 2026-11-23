import numpy as np, pandas as pd
import pytest

from bot.broker import OrderRejected
from bot.live import BybitBroker
from bot.runner import run_once

NO_FA = dict(mark_fn=lambda s: 100.0, funding_fn=lambda s, since: [], hourly_fn=lambda s, since: pd.DataFrame({'open': [], 'high': []}))
LOT = {"basePrecision": "0.000001", "minOrderQty": "0.000001", "minOrderAmt": "5"}


class FakeBybit:
    """最小的 Bybit 現貨替身:市價單立即成交,買單手續費扣幣、賣單扣 USDT,orderLinkId 不可重複。"""
    FEE = 0.001

    def __init__(self, usdt=1000.0, px=100.0, coins=None):
        self.bal = {"USDT": usdt, **(coins or {})}
        self.px, self.orders, self.placed = px, [], 0

    def _r(self, lst): return {"result": {"list": lst}}
    def get_tickers(self, category, symbol): return self._r([{"lastPrice": str(self.px), "bid1Price": str(self.px)}])
    def get_instruments_info(self, category, symbol): return self._r([{"status": "Trading", "lotSizeFilter": LOT}])
    def get_wallet_balance(self, accountType):
        return self._r([{"coin": [{"coin": k, "walletBalance": str(v), "locked": "0"} for k, v in self.bal.items()]}])
    def get_open_orders(self, **kw): return self._r([])

    def get_order_history(self, category, symbol, orderLinkId=None, limit=50):
        return self._r([o for o in self.orders if o["symbol"] == symbol and orderLinkId in (None, o["orderLinkId"])])

    def get_executions(self, category, symbol, orderId):
        o = next(x for x in self.orders if x["orderId"] == orderId)
        return self._r([o["_exec"]])

    def place_order(self, category, symbol, side, orderType, qty, orderLinkId, marketUnit=None):
        if any(o["orderLinkId"] == orderLinkId for o in self.orders):
            raise RuntimeError("Duplicate orderLinkId")
        base, q = symbol[:-4], float(qty)
        if side == "Buy":
            assert marketUnit == "quoteCoin"
            got, val = q / self.px, q
            fee, cur = got * self.FEE, base
            self.bal["USDT"] -= val; self.bal[base] = self.bal.get(base, 0.0) + got - fee
        else:
            assert q <= self.bal.get(base, 0.0) + 1e-12
            got, val = q, q * self.px
            fee, cur = val * self.FEE, "USDT"
            self.bal[base] -= q; self.bal["USDT"] += val - fee
        self.placed += 1
        self.orders.append({"orderId": f"id{self.placed}", "orderLinkId": orderLinkId, "symbol": symbol, "side": side,
                            "orderStatus": "Filled", "cumExecQty": str(got), "cumExecValue": str(val),
                            "_exec": {"execQty": str(got), "execPrice": str(self.px), "execFee": str(fee), "feeCurrency": cur}})
        return {"result": {"orderId": f"id{self.placed}"}}


def broker(c, day="2026-01-01", **kw):
    return BybitBroker(c, day, wait=0, **kw)


def test_buy_records_net_qty_and_sell_only_touches_bot_holdings():
    c = FakeBybit(coins={"BTC": 0.5})                      # 0.5 顆是使用者自己的,不是機器人的
    b = broker(c)
    f = b.buy_quote("BTCUSDT", 50.0)
    assert f.qty == pytest.approx(0.5 * 0.999) and b.holdings()["BTCUSDT"] == pytest.approx(0.4995)
    assert f.fee == pytest.approx(0.05)
    s = b.sell_qty("BTCUSDT", 10.0)                         # 要求賣超過機器人持有的量 -> 只賣機器人那部分
    assert s.qty == pytest.approx(0.4995) and "BTCUSDT" not in b.holdings()
    assert c.bal["BTC"] == pytest.approx(0.5)               # 使用者原有的沒動


def test_same_day_rerun_adopts_fill_instead_of_ordering_twice():
    c = FakeBybit()
    broker(c).buy_quote("BTCUSDT", 50.0)
    b2 = broker(c)                                          # 狀態沒存到就重跑:hold、orders 都是空的
    f = b2.buy_quote("BTCUSDT", 50.0)
    assert c.placed == 1 and b2.holdings()["BTCUSDT"] == pytest.approx(f.qty)
    with pytest.raises(OrderRejected):                      # 狀態有存到又重跑 (--force):不重複買
        broker(c, hold=b2.holdings(), orders=b2.orders).buy_quote("BTCUSDT", 50.0)
    assert c.placed == 1


def test_reconcile_recovers_lost_fill_next_day_and_caps_to_account_balance():
    c = FakeBybit()
    broker(c).buy_quote("BTCUSDT", 50.0)
    b = broker(c, day="2026-01-02")
    cost = {}
    notes = b.reconcile(cost, ["BTCUSDT"])
    assert b.holdings()["BTCUSDT"] == pytest.approx(0.4995) and cost["BTCUSDT"] == pytest.approx(50.0) and notes
    assert b.reconcile(cost, ["BTCUSDT"]) == []             # 已補登過就不再補
    c.bal["BTC"] = 0.2                                      # 使用者手動賣掉一部分
    b.reconcile(cost, ["BTCUSDT"])
    assert b.holdings()["BTCUSDT"] == pytest.approx(0.2) and cost["BTCUSDT"] == pytest.approx(50.0 * 0.2 / 0.4995)


def test_small_orders_rejected_and_tiny_remainder_sold_together():
    c = FakeBybit()
    b = broker(c)
    with pytest.raises(OrderRejected):
        b.buy_quote("BTCUSDT", 4.0)
    b.buy_quote("BTCUSDT", 12.0)
    b.day = "2026-01-02"
    s = b.sell_qty("BTCUSDT", 0.08)                         # 剩 ~4 USDT 之後賣不掉 -> 一次賣完
    assert s.qty == pytest.approx(0.11988) and not b.holdings()


def df(trend, n=260, base=100.0):
    c = base * (1 + trend) ** np.arange(n)
    idx = pd.date_range("2025-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c}, index=idx)


def test_run_once_live_buys_then_exits_and_refuses_paper_state(tmp_path):
    db, c = str(tmp_path / "live.db"), FakeBybit(usdt=10_000.0)
    up, down = (lambda s: df(0.004)), (lambda s: df(-0.004))
    r = run_once(db, up, lambda s: 100.0, **NO_FA, day="2026-01-01", live=True, client=c)
    assert r["mode"] == "live" and c.placed == 2 and c.bal["USDT"] < 10_000
    assert r["equity"] == pytest.approx(10_000, rel=0.01)
    assert run_once(db, up, lambda s: 100.0, **NO_FA, day="2026-01-01", live=True, client=c)["skipped"]
    r2 = run_once(db, down, lambda s: 100.0, **NO_FA, day="2026-01-02", live=True, client=c)
    assert len([a for a in r2["actions"] if a.startswith("出場")]) == 2 and c.placed == 4
    assert c.bal["BTC"] < 1e-6 and c.bal["ETH"] < 1e-6
    with pytest.raises(SystemExit):                         # 實盤狀態檔不能拿去跑模擬
        run_once(db, up, lambda s: 100.0, **NO_FA, day="2026-01-03")
    paper = str(tmp_path / "paper.db")
    run_once(paper, up, lambda s: 100.0, **NO_FA, day="2026-01-01")
    with pytest.raises(SystemExit):                         # 模擬狀態檔也不能拿去跑實盤
        run_once(paper, up, lambda s: 100.0, **NO_FA, day="2026-01-02", live=True, client=c)
    assert c.placed == 4


def test_run_once_live_crash_before_save_does_not_double_buy(tmp_path):
    db, c = str(tmp_path / "live.db"), FakeBybit(usdt=10_000.0)
    up = lambda s: df(0.004)
    broker(c).buy_quote("BTCUSDT", 100.0)                   # 模擬:下單後狀態沒存到 (同一天)
    r = run_once(db, up, lambda s: 100.0, **NO_FA, day="2026-01-01", live=True, client=c)
    assert any(a.startswith("補登 BTCUSDT") for a in r["actions"])
    assert len([o for o in c.orders if o["symbol"] == "BTCUSDT"]) == 1
