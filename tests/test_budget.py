from bot.budget import DailyBudget


def mk():
    return DailyBudget(":memory:", 0.05, 0.30)


def test_allowance_is_5pct_and_fixed_within_day():
    b = mk()
    assert b.open_day(10_000, "2026-11-08")["allowance"] == 500
    assert b.open_day(99_999, "2026-11-08")["allowance"] == 500   # 同日不重算


def test_next_day_recomputed_and_carry_committed():
    b = mk()
    b.open_day(10_000, "2026-11-08")
    assert b.reserve(200, "2026-11-08")
    d2 = b.open_day(8_000, "2026-11-09")
    assert d2["allowance"] == 400 and d2["committed"] == 200
    assert b.available("2026-11-09") == 200


def test_cannot_exceed_allowance():
    b = mk()
    b.open_day(10_000, "d")
    assert b.reserve(500, "d")
    assert not b.reserve(1, "d")


def test_halt_at_30pct_of_allowance():
    b = mk()
    b.open_day(10_000, "d")           # allowance 500 -> 熔斷線 -150
    assert not b.check_halt(-149, "d")
    assert b.check_halt(-150, "d")
    assert b.available("d") == 0 and not b.reserve(10, "d")
