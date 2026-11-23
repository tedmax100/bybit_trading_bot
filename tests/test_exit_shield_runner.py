import pytest
import pandas as pd
import numpy as np
from bot.runner import run_once
from tests.test_runner import NO_FA, _kv


def df_uptrend(n=260, base=100.0):
    """帶微幅波動的上升趨勢, RSI 約 60~65, w 為 1.0"""
    c = base * (1.001 ** np.arange(n)) + 1.5 * np.sin(np.arange(n))
    idx = pd.date_range("2025-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c}, index=idx)


def test_runner_triggers_hard_stop_loss(tmp_path):
    db = str(tmp_path / "sl.db")
    up = lambda s: df_uptrend()
    # Day 1: enter in uptrend at price 100
    r1 = run_once(db, up, lambda s: 100.0, **NO_FA, exit_shield_enabled=True, day="2026-01-01")
    assert any("進場 BTCUSDT" in a for a in r1["actions"])
    hold1 = _kv(db, "paper")["hold"]
    assert hold1["BTCUSDT"] > 0

    # Day 2: price plunges to 96.0 (-4.0% loss, sl_pct=3.0) while macro trend is still positive
    r2 = run_once(db, up, lambda s: 96.0, **NO_FA, exit_shield_enabled=True, day="2026-01-02")
    assert any("防護盾: 自適應停損觸發" in a for a in r2["actions"])
    hold2 = _kv(db, "paper")["hold"]
    assert hold2.get("BTCUSDT", 0.0) == 0.0


def test_runner_triggers_break_even(tmp_path):
    db = str(tmp_path / "be.db")
    up = lambda s: df_uptrend()
    # Day 1: enter at 100
    run_once(db, up, lambda s: 100.0, **NO_FA, exit_shield_enabled=True, day="2026-01-01")

    # Day 2: price rallies to 103.0 (+3.0% gain > be_trigger 2.5%), sets peak_px to 103.0
    r2 = run_once(db, up, lambda s: 103.0, **NO_FA, exit_shield_enabled=True, day="2026-01-02")
    peak2 = _kv(db, "paper")["peak_px"]
    assert peak2["BTCUSDT"] == 103.0

    # Day 3: price falls back to 100.2 (+0.2% <= be_floor 0.3%)
    r3 = run_once(db, up, lambda s: 100.2, **NO_FA, exit_shield_enabled=True, day="2026-01-03")
    assert any("防護盾: 保本鎖利觸發" in a for a in r3["actions"])
    hold3 = _kv(db, "paper")["hold"]
    assert hold3.get("BTCUSDT", 0.0) == 0.0


def test_runner_triggers_trailing_tp(tmp_path):
    db = str(tmp_path / "tp.db")
    up = lambda s: df_uptrend()
    # Day 1: enter at 100
    run_once(db, up, lambda s: 100.0, **NO_FA, exit_shield_enabled=True, day="2026-01-01")

    # Day 2: price rallies to 106.0 (+6.0% gain >= tp_trigger 4.5%)
    run_once(db, up, lambda s: 106.0, **NO_FA, exit_shield_enabled=True, day="2026-01-02")

    # Day 3: price pulls back to 104.0 (pullback 2.0% >= 1.5%)
    r3 = run_once(db, up, lambda s: 104.0, **NO_FA, exit_shield_enabled=True, day="2026-01-03")
    assert any("防護盾: 動態移動停利觸發" in a for a in r3["actions"])
    hold3 = _kv(db, "paper")["hold"]
    assert hold3.get("BTCUSDT", 0.0) == 0.0
