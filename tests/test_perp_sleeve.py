import json, sqlite3
import numpy as np, pandas as pd, pytest
from bot import perp_sleeve as ps
from bot.perp_sleeve import K, PaperExec, decide, perp_step


def bars(closes):
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="4h", tz="UTC")
    c = np.asarray(closes, float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c}, index=idx)


def trend(rate, n=1300, tail=()):
    return bars(list(100 * (1 + rate) ** np.arange(n)) + list(tail))


UP, DOWN = trend(0.0005), trend(-0.0005)
NOF = dict(funding_fn=lambda s, since: [])


def st(db):
    return json.loads(sqlite3.connect(db).execute("SELECT v FROM kv WHERE k='psleeve'").fetchone()[0])


def test_decide_needs_regime_and_breakout():
    assert decide(UP, 0, 0)[0] == 1                       # 多頭情境 + 創 5 日新高
    assert decide(DOWN, 0, 0)[0] == -1                    # 空頭情境 + 創 5 日新低
    last = UP["close"].iloc[-1]
    assert decide(trend(0.0005, tail=[last * 0.999]), 0, 0)[0] == 0      # 多頭情境但沒有突破 -> 不進場
    assert decide(trend(0.0005, tail=[last * 0.90]), 0, 0)[0] == 0       # 多頭情境下的急跌不放空
    assert decide(bars([100.0] * 300), 0, 0) == (0, "資料不足")


def test_decide_exits():
    last = UP["close"].iloc[-1]
    assert decide(UP, 1, 3)[0] == 1                                        # 續抱
    assert decide(trend(0.0005, tail=[last * 0.98]), 1, 3)[0] == 0         # 跌破 2 日低
    assert decide(UP, 1, 5 * K)[0] == 0                                    # 持有滿 5 天
    assert decide(UP, -1, 1)[0] == 0                                       # 空單遇到趨勢翻多


def test_step_opens_once_per_bar_then_closes_with_pnl(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "QTY", {"BTCUSDT": 0.001})
    db = str(tmp_path / "p.db")
    px = float(UP["close"].iloc[-1])
    r = perp_step(db, lambda s: UP, lambda s: px, **NOF)
    assert r["pos"] == {"BTCUSDT": "多"} and len(r["actions"]) == 1
    assert perp_step(db, lambda s: UP, lambda s: px, **NOF)["skipped"]       # 同一根 K 線不重複決策
    nxt = trend(0.0005, tail=[px * 0.98])                                    # 下一根跌破 2 日低 -> 平倉
    r2 = perp_step(db, lambda s: nxt, lambda s: px * 0.98, **NOF)
    s = st(db)
    assert r2["pos"] == {} and s["trades"] == 1 and s["wins"] == 0
    assert s["realized"] == pytest.approx(0.001 * px * (0.98 * (1 - 0.0005) - 1.0005) - s["fees"], rel=1e-6)
    assert s["realized"] < 0


def test_short_collects_positive_funding_and_long_pays(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "QTY", {"BTCUSDT": 0.001})
    db = str(tmp_path / "f.db")
    px = float(DOWN["close"].iloc[-1])
    perp_step(db, lambda s: DOWN, lambda s: px, **NOF)
    nxt = trend(-0.0005, n=1301)
    px2 = float(nxt["close"].iloc[-1])
    perp_step(db, lambda s: nxt, lambda s: px2, funding_fn=lambda s, since: [(since + 1, 0.001)])
    assert st(db)["funding"] == pytest.approx(0.001 * px2 * 0.001)


def test_paper_liquidation_loses_margin(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "QTY", {"BTCUSDT": 0.001})
    db = str(tmp_path / "l.db")
    px = float(UP["close"].iloc[-1])
    perp_step(db, lambda s: UP, lambda s: px, **NOF)
    crash = trend(0.0005, tail=[px * 0.45])
    r = perp_step(db, lambda s: crash, lambda s: px * 0.45, **NOF)
    s = st(db)
    assert s["liqs"] == 1 and s["pos"] == {} and any("爆倉" in a for a in r["actions"])
    assert s["realized"] == pytest.approx(-0.001 * px * 1.0005 / 2 - s["fees"], rel=1e-6)


def test_live_db_without_flag_never_trades(tmp_path, monkeypatch):
    db = str(tmp_path / "live.db")
    c = sqlite3.connect(db); c.execute("CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT)")
    c.execute("INSERT INTO kv VALUES('mode','\"live\"')"); c.commit(); c.close()
    monkeypatch.setattr(ps, "PERP_LIVE", False)
    assert perp_step(db, lambda s: UP, lambda s: 1.0, **NOF)["skipped"]
    with pytest.raises(SystemExit):                                           # 模擬執行器不能寫進實盤狀態檔
        perp_step(db, lambda s: UP, lambda s: 1.0, executor=PaperExec(lambda s: 1.0), **NOF)


class FakeClient:
    def __init__(self): self.orders, self.size, self.side = {}, 0.0, ""
    def set_leverage(self, **k): raise Exception("leverage not modified (ErrCode: 110043)")
    def get_tickers(self, **k): return {"result": {"list": [{"markPrice": "100"}]}}
    def get_instruments_info(self, **k): return {"result": {"list": [{"priceFilter": {"tickSize": "0.10"}}]}}
    def get_open_orders(self, **k): return {"result": {"list": []}}
    def get_order_history(self, orderLinkId=None, **k):
        return {"result": {"list": [self.orders[orderLinkId]] if orderLinkId in self.orders else []}}
    def get_positions(self, symbol=None, **k):
        mine = symbol == getattr(self, "sym", symbol)                        # 只有下過單的那一檔有部位
        return {"result": {"list": [{"size": str(self.size if mine else 0.0), "side": self.side if mine else ""}]}}
    def place_order(self, **k):
        self.sym = k["symbol"]
        self.orders[k["orderLinkId"]] = {**k, "orderStatus": "Filled", "avgPrice": "100", "cumExecFee": "0.01"}
        if k.get("reduceOnly"): self.size, self.side = 0.0, ""
        else: self.size, self.side = float(k["qty"]), k["side"]


def test_live_exec_orders_are_idempotent_with_stop_and_reduce_only():
    c = FakeClient(); ex = ps.LiveExec(c, wait=0)
    assert ex.open("ETHUSDT", -1, 0.01, "2026010100") == (100.0, 0.01)
    o = c.orders["bybotp-2026010100-ETHUSDT-O"]
    assert o["side"] == "Sell" and o["stopLoss"] == "110.00" and o["category"] == "linear" and o["qty"] == "0.01"
    ex.open("ETHUSDT", -1, 0.01, "2026010100")                                # 重跑:認領同一筆,不再下單
    assert len(c.orders) == 1 and ex.position("ETHUSDT") == -0.01
    ex.close("ETHUSDT", -1, 0.01, "2026010104")
    o2 = c.orders["bybotp-2026010104-ETHUSDT-C"]
    assert o2["side"] == "Buy" and o2["reduceOnly"] is True and ex.position("ETHUSDT") == 0.0


def test_live_step_sets_leverage_opens_then_reconciles_stop_out(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "QTY", {"BTCUSDT": 0.001})
    db = str(tmp_path / "live.db")
    con = sqlite3.connect(db); con.execute("CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT)")
    con.execute("INSERT INTO kv VALUES('mode','\"live\"')"); con.commit(); con.close()
    c = FakeClient(); ex = ps.LiveExec(c, wait=0)
    r = perp_step(db, lambda s: DOWN, lambda s: 100.0, executor=ex, **NOF)
    assert r["pos"] == {"BTCUSDT": "空"} and c.size == 0.001 and c.side == "Sell"
    assert st(db)["pos"]["BTCUSDT"]["entry"] == 100.0
    c.size, c.side = 0.0, ""                                                  # 交易所的保護性停損被觸發
    nxt = trend(-0.0005, n=1301)
    r2 = perp_step(db, lambda s: nxt, lambda s: 110.0, executor=ex, **NOF)
    s = st(db)
    assert s["pos"] == {} and s["trades"] == 1 and any("對帳" in a for a in r2["actions"])
    assert len(c.orders) == 1                                                 # 沒有再送平倉單,這一根也不再開新倉


def test_state_is_saved_right_after_fill_even_if_later_symbol_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "QTY", {"BTCUSDT": 0.001, "ETHUSDT": 0.01})
    db = str(tmp_path / "live.db")
    con = sqlite3.connect(db); con.execute("CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT)")
    con.execute("INSERT INTO kv VALUES('mode','\"live\"')"); con.commit(); con.close()

    class Flaky(ps.LiveExec):
        def open(self, sym, side, qty, tag):
            if sym == "ETHUSDT":
                raise RuntimeError("boom")
            return super().open(sym, side, qty, tag)
    with pytest.raises(RuntimeError):
        perp_step(db, lambda s: DOWN, lambda s: 100.0, executor=Flaky(FakeClient(), wait=0), **NOF)
    assert list(st(db)["pos"]) == ["BTCUSDT"]                                 # BTC 已成交的部位有存下來
