import pytest
from bot.broker import GuardedBroker, OrderRejected, PaperBroker
from bot.budget import DailyBudget, utc_day


def setup(equity=10_000, px=100.0):
    paper = PaperBroker(price_fn=lambda s: px, cash=equity)
    b = DailyBudget(":memory:", 0.05, 0.30)
    b.open_day(equity)
    return GuardedBroker(paper, b), b, paper


def test_rejects_non_whitelist():
    g, *_ = setup()
    with pytest.raises(OrderRejected):
        g.buy_quote("DOGEUSDT", 100)


def test_rejects_below_min_and_over_budget():
    g, b, _ = setup()                      # 額度 500
    with pytest.raises(OrderRejected):
        g.buy_quote("BTCUSDT", 1)
    g.buy_quote("BTCUSDT", 500)
    with pytest.raises(OrderRejected):
        g.buy_quote("ETHUSDT", 10)
    assert b.available() == 0


def test_sell_releases_budget_and_records_pnl():
    px = {"v": 100.0}
    paper = PaperBroker(price_fn=lambda s: px["v"], cash=10_000)
    b = DailyBudget(":memory:", 0.05, 0.30); b.open_day(10_000)
    g = GuardedBroker(paper, b)
    f = g.buy_quote("BTCUSDT", 400)
    px["v"] = 90.0                          # 跌 10% -> 虧約 40 + 成本
    g.sell_qty("BTCUSDT", f.qty)
    d = b.get()
    assert d["committed"] == pytest.approx(0, abs=1e-6)
    assert d["realized_pnl"] < -40
    assert b.available() == pytest.approx(500, abs=1e-6)


def test_halt_blocks_new_buys_but_not_exit():
    px = {"v": 100.0}
    paper = PaperBroker(price_fn=lambda s: px["v"], cash=10_000)
    b = DailyBudget(":memory:", 0.05, 0.30); b.open_day(10_000)   # 熔斷線 -150
    g = GuardedBroker(paper, b)
    f = g.buy_quote("BTCUSDT", 500)
    px["v"] = 60.0                          # 大跌 40% -> 虧約 200
    g.sell_qty("BTCUSDT", f.qty)            # 出場仍可執行
    assert b.get()["halted"] == 1
    with pytest.raises(OrderRejected):
        g.buy_quote("BTCUSDT", 10)
