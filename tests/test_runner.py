import pytest
import numpy as np, pandas as pd
from bot.runner import run_once

NO_FA = dict(mark_fn=lambda s: 100.0, funding_fn=lambda s, since: [], hourly_fn=lambda s, since: pd.DataFrame({'open': [], 'high': []}))


def df(trend, n=260, base=100.0):
    c = base * (1 + trend) ** np.arange(n)
    idx = pd.date_range("2025-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c}, index=idx)


def test_enters_in_uptrend_once_per_day_and_exits_in_downtrend(tmp_path):
    db = str(tmp_path / "t.db")
    up, down = (lambda s: df(0.004)), (lambda s: df(-0.004))
    r = run_once(db, up, lambda s: 100.0, **NO_FA, day="2026-01-01")
    assert len([a for a in r["actions"] if a.startswith("進場")]) == 2
    assert run_once(db, up, lambda s: 100.0, **NO_FA, day="2026-01-01")["skipped"]          # 冪等
    r2 = run_once(db, down, lambda s: 100.0, **NO_FA, day="2026-01-02")
    assert len([a for a in r2["actions"] if a.startswith("出場")]) == 2


def run_fa(*a, **k):
    return run_once(*a, fa_enabled=True, **k)


def _h(high):
    idx = pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC")
    return pd.DataFrame({"open": [100.0] * 3, "high": [high] * 3}, index=idx)


def _kv(db, k):
    import json, sqlite3
    return json.loads(sqlite3.connect(db).execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()[0])


def test_fa_opens_hedged_collects_funding_and_ma_exit_does_not_touch_spot_leg(tmp_path):
    db = str(tmp_path / "f.db")
    up, down = (lambda s: df(0.004)), (lambda s: df(-0.004))
    r = run_fa(db, up, lambda s: 100.0, **NO_FA, day="2026-01-01", now_ms=1_000)
    assert len([a for a in r["actions"] if a.startswith("套利開倉")]) == 2
    fa1, perp1 = _kv(db, "fa"), _kv(db, "perp")
    for s in ("BTCUSDT", "ETHUSDT"):                       # 對沖:現貨量 == 永續空單量
        assert perp1[s]["qty"] == pytest.approx(fa1["spot"][s])
    cash0 = _kv(db, "paper")["cash"]
    funding = lambda s, since: [(2_000, 0.0001)]
    r2 = run_fa(db, down, lambda s: 100.0, mark_fn=lambda s: 100.0, funding_fn=funding, hourly_fn=NO_FA["hourly_fn"],
                  day="2026-01-02", now_ms=3_000)
    assert len([a for a in r2["actions"] if a.startswith("出場")]) == 2          # 均線出場
    hold = _kv(db, "paper")["hold"]
    for s in ("BTCUSDT", "ETHUSDT"):                       # 套利現貨腿還在
        assert hold[s] == pytest.approx(fa1["spot"][s])
    assert _kv(db, "paper")["cash"] > cash0                # funding 入帳 + 均線出場回收
    assert _kv(db, "perp")["BTCUSDT"]["funding"] > 0


def test_fa_liquidation_forfeits_margin_and_flattens_spot(tmp_path):
    db = str(tmp_path / "l.db")
    up = lambda s: df(0.004)
    run_fa(db, up, lambda s: 100.0, **NO_FA, day="2026-01-01", now_ms=1_000)
    r = run_fa(db, up, lambda s: 100.0, mark_fn=lambda s: 100.0, funding_fn=NO_FA["funding_fn"],
                 hourly_fn=lambda s, since: _h(200.0), day="2026-01-02", now_ms=3_000)
    assert len([a for a in r["actions"] if a.startswith("套利爆倉")]) == 2
    fa = _kv(db, "fa")
    assert fa["liqs"] == 2 and fa["spot"] == {} and _kv(db, "perp") == {}


def test_fa_recenters_when_price_rallies_30pct(tmp_path):
    db = str(tmp_path / "r.db")
    up = lambda s: df(0.004)
    run_fa(db, up, lambda s: 100.0, **NO_FA, day="2026-01-01", now_ms=1_000)
    r = run_fa(db, up, lambda s: 140.0, mark_fn=lambda s: 140.0, funding_fn=NO_FA["funding_fn"],
                 hourly_fn=NO_FA["hourly_fn"], day="2026-01-02", now_ms=3_000)
    assert len([a for a in r["actions"] if a.startswith("套利重新置中")]) == 2
    assert _kv(db, "fa")["recenters"] == 2 and len(_kv(db, "perp")) == 2     # 平倉後重開


def test_fa_disabled_by_default_and_unwinds_existing_positions(tmp_path):
    db = str(tmp_path / "d.db")
    up = lambda s: df(0.004)
    r0 = run_once(db, up, lambda s: 100.0, **NO_FA, day="2026-01-01", now_ms=1_000)      # 預設停用:不開套利
    assert not any("套利" in a for a in r0["actions"]) and _kv(db, "perp") == {}
    db2 = str(tmp_path / "e.db")
    run_fa(db2, up, lambda s: 100.0, **NO_FA, day="2026-01-01", now_ms=1_000)           # 先啟用開倉
    assert len(_kv(db2, "perp")) == 2
    r = run_once(db2, up, lambda s: 100.0, **NO_FA, day="2026-01-02", now_ms=3_000)     # 再停用 -> 平倉
    assert len([a for a in r["actions"] if a.startswith("套利停用平倉")]) == 2
    assert _kv(db2, "perp") == {} and _kv(db2, "fa")["spot"] == {}


def test_shadow_portfolios_track_weights_and_pnl_without_trading(tmp_path):
    db = str(tmp_path / "s.db")
    up, down = (lambda s: df(0.004)), (lambda s: df(-0.004))
    r = run_once(db, up, lambda s: 100.0, **NO_FA, day="2026-01-01")
    for v in r["shadow_pf"].values():                      # 上升趨勢、零波動 -> 兩個組合都滿倉 70/30,只扣進場成本
        assert v["w"] == {"BTCUSDT": 0.7, "ETHUSDT": 0.3} and v["idx"] == pytest.approx(100 * (1 - 0.0015), abs=0.01)
    cash = _kv(db, "paper")["cash"]
    r2 = run_once(db, down, lambda s: 110.0, **NO_FA, day="2026-01-02")
    for v in r2["shadow_pf"].values():                     # 先吃到 +10%,再因轉空全數出場
        assert v["w"] == {"BTCUSDT": 0.0, "ETHUSDT": 0.0}
        assert v["idx"] == pytest.approx(100 * (1 - 0.0015) * 1.10 * (1 - 0.0015), abs=0.01)
    assert _kv(db, "paper")["cash"] > cash                 # 觀察組合不佔用模擬帳戶 (現金只因均線出場而增加)
    assert "觀察組合" in __import__("bot.runner", fromlist=["report"]).report(db)


def _noisy(n=260, amp=0.05):
    """上升趨勢 + 高波動:趨勢分數滿分,但波動目標把部位縮到一部分。"""
    r = 0.004 + amp * np.where(np.arange(n) % 2 == 0, 1, -1)
    c = 100.0 * np.cumprod(1 + r)
    idx = pd.date_range("2025-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c}, index=idx)


def test_position_scales_with_volatility_and_trims_when_vol_rises(tmp_path):
    db = str(tmp_path / "v.db")
    r = run_once(db, lambda s: df(0.004), lambda s: 100.0, **NO_FA, day="2026-01-01")      # 低波動 -> 滿額
    assert r["signals"]["BTCUSDT"]["w"] == 1.0
    full = _kv(db, "paper")["hold"]["BTCUSDT"]
    r2 = run_once(db, lambda s: _noisy(), lambda s: 100.0, **NO_FA, day="2026-01-02")      # 波動飆高 -> 減碼但不出清
    w = r2["signals"]["BTCUSDT"]["w"]
    assert 0 < w < 0.6 and len([a for a in r2["actions"] if a.startswith("減碼")]) == 2
    assert _kv(db, "paper")["hold"]["BTCUSDT"] == pytest.approx(full * w, rel=0.03)
    r3 = run_once(db, lambda s: _noisy(), lambda s: 100.0, **NO_FA, day="2026-01-03")      # 目標沒變 -> 不動
    assert r3["actions"] == []
    r4 = run_once(db, lambda s: df(0.004), lambda s: 100.0, **NO_FA, day="2026-01-04")     # 波動回落 -> 加碼回去
    assert len([a for a in r4["actions"] if a.startswith("加碼")]) == 2


def test_perf_windows_use_snapshot_n_days_back_and_flag_short_history(tmp_path):
    import sqlite3
    from bot.runner import perf
    db = str(tmp_path / "p.db")
    run_once(db, lambda s: df(-0.004), lambda s: 100.0, **NO_FA, day="2026-01-01")      # 空手,權益 10000
    con = sqlite3.connect(db)
    for d, e in [("2026-01-02", 10100.0), ("2026-01-09", 10200.0), ("2026-01-10", 10500.0)]:
        con.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?)", (d, e, e, "{}", "[]", "{}", 0, 100.0))
    con.execute("UPDATE kv SET v=? WHERE k='paper'", ('{"cash": 10710.0, "hold": {}, "cost": {}}',)); con.commit()
    lines = perf(db, lambda s: 110.0, today="2026-01-10").splitlines()
    row = {l.split()[0]: l for l in lines[2:]}
    assert "+210.00" in row["當日"] and "+2.00%" in row["當日"]          # 對 01-10 的 10500
    assert "2026-01-02" in row["7日"] and "+610.00" in row["7日"]        # 7 天前最近的一筆是 01-02
    assert "紀錄不足" in row["30日"] and "+710.00" in row["30日"] and "+10.00%" in row["30日"]   # BTC 100 -> 110


def test_new_shadow_portfolio_forks_from_existing_one_and_uses_wider_band(tmp_path, monkeypatch):
    import bot.runner as R
    db = str(tmp_path / "f.db")
    base = "D 趨勢分數+波動目標(BTC70/ETH30)"
    w = {"v": 1.0}
    monkeypatch.setattr(R, "SHADOW_PF", {base: lambda d: w["v"]})
    monkeypatch.setattr(R, "SHADOW_PF_FORK", {})
    run_once(db, lambda s: df(0.004), lambda s: 100.0, **NO_FA, day="2026-01-01")
    monkeypatch.setattr(R, "SHADOW_PF", {base: lambda d: w["v"], "寬": lambda d: w["v"]})
    monkeypatch.setattr(R, "SHADOW_PF_FORK", {"寬": base})
    monkeypatch.setattr(R, "SHADOW_PF_BAND", {"寬": 0.20})
    w["v"] = 0.9                                            # 變動 10%:5% 門檻會調,20% 門檻不動
    r = run_once(db, lambda s: df(0.004), lambda s: 110.0, **NO_FA, day="2026-01-02")["shadow_pf"]
    assert r[base]["w"]["BTCUSDT"] == pytest.approx(0.63) and r["寬"]["w"]["BTCUSDT"] == pytest.approx(0.7)
    assert _kv(db, "shadow_pf")["寬"]["days"] == 2          # 沿用分叉來源的紀錄,不是從 100 重新起算
    assert r["寬"]["idx"] == pytest.approx(100 * (1 - 0.0015) * 1.10, abs=0.01)
    w["v"] = 0.5                                            # 累積變動超過 20% -> 也跟著調
    r = run_once(db, lambda s: df(0.004), lambda s: 110.0, **NO_FA, day="2026-01-03")["shadow_pf"]
    assert r["寬"]["w"]["BTCUSDT"] == pytest.approx(0.35)


def test_recorded_equity_and_perf_include_perp_sleeve_pnl(tmp_path):
    import json, sqlite3
    from bot.runner import perf
    db = str(tmp_path / "e.db")
    flat = lambda s: df(-0.004)
    r0 = run_once(db, flat, lambda s: 100.0, **NO_FA, day="2026-01-01")                 # 空手,權益 10000
    con = sqlite3.connect(db)
    ps = {"pos": {"BTCUSDT": {"side": -1, "qty": 2.0, "entry": 100.0, "margin": 100.0}}, "realized": -1.0, "unreal": 0.0}
    con.execute("INSERT INTO kv(k,v) VALUES('psleeve',?)", (json.dumps(ps),)); con.commit()
    r1 = run_once(db, flat, lambda s: 90.0, **{**NO_FA, "mark_fn": lambda s: 90.0}, day="2026-01-02")
    assert r1["equity"] == pytest.approx(r0["equity"] + 2.0 * 10 - 1.0)                 # 模擬:未實現 +20、已實現 -1
    assert r1["allowance"] == pytest.approx(r0["allowance"])                            # 額度的算法不變
    assert "+19.00" in perf(db, lambda s: 90.0, today="2026-01-02").splitlines()[3]
    con.execute("UPDATE kv SET v='\"live\"' WHERE k='mode'"); con.commit()            # 實盤:已實現已在錢包餘額裡,只加未實現
    assert "+20.00" in perf(db, lambda s: 90.0, today="2026-01-02").splitlines()[3]


def _h4(n=200 * 6 + 10, step=0.0008):
    c = 100.0 * np.cumprod(np.full(n, 1 + step))
    idx = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c}, index=idx)


def test_shadow_uses_day_based_params_on_4h_bars():
    from bot.runner import _ma_on, _trend_w
    up = _h4()
    assert _ma_on(up) == 1.0 and _trend_w(up) == pytest.approx(1.0)
    # 最後 40 天轉跌:200 根 4H (約 33 天) 的均線會翻空,但 200 日均線/長期動能仍多頭 -> 不能歸零
    c = up["close"].to_numpy().copy()
    c[-240:] = c[-241] * np.cumprod(np.full(240, 1 - 0.0006))
    mixed = up.assign(close=c)
    assert _ma_on(mixed) == 1.0
    assert 0 < _trend_w(mixed) < 1.0
    short = _h4(100)                                        # 資料不足 200 天 -> NaN,不是假訊號
    assert _trend_w(short) != _trend_w(short)


def test_stale_shadow_records_are_archived_and_restarted(tmp_path):
    import json, sqlite3
    db = str(tmp_path / "v.db")
    run_once(db, lambda s: df(0.004), lambda s: 100.0, **NO_FA, day="2026-01-01")
    con = sqlite3.connect(db)
    con.execute("DELETE FROM kv WHERE k='shadow_ver'")
    con.execute("UPDATE kv SET v=? WHERE k='shadow_pf'", (json.dumps({"舊": {"idx": 50.0, "w": {}, "last": {}, "turn": 9.0, "days": 99}}),))
    con.commit()
    r = run_once(db, lambda s: df(0.004), lambda s: 100.0, **NO_FA, day="2026-01-02")
    assert _kv(db, "shadow_pf_v1_invalid")["舊"]["idx"] == 50.0
    assert "舊" not in r["shadow_pf"] and all(v["idx"] > 99 for v in r["shadow_pf"].values())
    assert all(v["since"] == "2026-01-02" for v in _kv(db, "shadow_pf").values())
    rep = __import__("bot.runner", fromlist=["report"]).report(db)
    assert "自 2026-01-02 起" in rep
