import pytest
from bot.strategies.exit_shield import evaluate_exit_shield, ExitDecision


def test_no_exit_when_normal():
    # Entry 100, current 101, peak 102 (+1% gain)
    d = evaluate_exit_shield(current_price=101.0, entry_price=100.0, peak_price=102.0)
    assert not d.triggered
    assert d.pnl_pct == pytest.approx(1.0)
    assert d.peak_pct == pytest.approx(2.0)


def test_hard_stop_loss_trigger():
    # Entry 100, current 96.5 (-3.5% loss with sl_pct=3.0)
    d = evaluate_exit_shield(current_price=96.5, entry_price=100.0, peak_price=100.0, sl_pct=3.0)
    assert d.triggered
    assert d.action == "STOP_LOSS"
    assert "虧損 -3.50%" in d.reason


def test_break_even_trigger():
    # Entry 100, peak was 103.0 (+3.0% > be_trigger 2.5%), current is 100.2 (+0.2% <= be_floor 0.3%)
    d = evaluate_exit_shield(current_price=100.2, entry_price=100.0, peak_price=103.0,
                             be_trigger_pct=2.5, be_floor_pct=0.3)
    assert d.triggered
    assert d.action == "BREAK_EVEN"
    assert "保本鎖利觸發" in d.reason


def test_trailing_take_profit_trigger():
    # Entry 100, peak was 106.0 (+6.0% >= tp_trigger 4.5%), current is 104.2 (pullback = 1.8% >= 1.5%)
    d = evaluate_exit_shield(current_price=104.2, entry_price=100.0, peak_price=106.0,
                             tp_trigger_pct=4.5, tp_pullback_pct=1.5)
    assert d.triggered
    assert d.action == "TRAILING_TP"
    assert "動態移動停利觸發" in d.reason


def test_rsi_exhaustion_trigger():
    # Entry 100, current 103 (+3% gain >= be_trigger 2.5%), 4H RSI = 82 >= 80
    d = evaluate_exit_shield(current_price=103.0, entry_price=100.0, peak_price=103.0,
                             rsi=82.0, rsi_tp_threshold=80.0)
    assert d.triggered
    assert d.action == "RSI_TP"
    assert "RSI 極端超買" in d.reason
