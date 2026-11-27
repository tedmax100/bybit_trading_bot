"""每日任務 (模擬交易):結算額度 -> 算趨勢分數×波動目標 -> 調整部位 -> 記錄。
冪等:同一個 UTC 日只執行一次,所以可以每小時觸發,電腦關機漏跑也會在開機後補上當天的。
現貨部位佔每日額度的 50% (BTC 70% / ETH 30%);另一半留給選配的資金費率套利 (預設停用)。"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Callable

import pandas as pd

from . import exchange
from .broker import GuardedBroker, OrderRejected, PaperBroker
from .budget import DailyBudget, utc_day
from .perp import PerpBook
from .perp_sleeve import sleeve_equity
from .config import MIN_ORDER_USDT, SETTINGS
from .strategies.dynamic import trend_vol
from .strategies.exit_shield import evaluate_exit_shield
from .strategies.ma_filter import MAFilter

SLEEVE = {"BTCUSDT": 0.35, "ETHUSDT": 0.15}          # 佔每日額度的比例 (合計 50%,另一半留給尚未模擬的套利)
if os.getenv("SLEEVE"):                               # 例: SLEEVE=BTCUSDT:0.7,ETHUSDT:0.3 (實盤 Job 用)
    SLEEVE = {k: float(v) for k, v in (x.split(":") for x in os.environ["SLEEVE"].split(","))}
    assert sum(SLEEVE.values()) <= 1 + 1e-9, "SLEEVE 合計不可超過 1"
LIVE = os.getenv("LIVE", "0") == "1"                  # 1 = 用真錢在 Bybit 下現貨單 (需 BYBIT_ENV 與金鑰)
FA_ENABLED = os.getenv("FA_ENABLED", "0") == "1"      # 資金費率套利為選配,預設停用
FA_SYMS = {"BTCUSDT": 0.25, "ETHUSDT": 0.25}      # 套利佔每日額度的比例 (合計 50%)
FA_LEV = 1.5                                       # 永續槓桿 (強平約在進場價 +66%)
FA_RECENTER = 0.30                                 # 價格較永續進場價上漲 30% -> 兩腿平倉重開
SHADOW = ["SOLUSDT", "LINKUSDT", "AAVEUSDT", "XRPUSDT"]   # 觀察模式:只記錄虛擬績效,不佔額度、不下單
# 觀察組合:整份虛擬資金 (起算 100) 依目標權重配置,用來並排比較「舊的均線策略」與現行的動態策略 (D)
SHADOW_MIX = {"BTCUSDT": 0.7, "ETHUSDT": 0.3}
SHADOW_PF_RENAMED = {"現行 均線(BTC70/ETH30)": "舊 均線(BTC70/ETH30)"}   # 舊名稱 -> 新名稱;沿用已累積的紀錄


def _bars_per_day(df: pd.DataFrame) -> int:
    """日線 -> 1,4 小時線 -> 6。觀察用的參數都以「天」為單位,要乘上這個數才對得上 K 線根數。"""
    if len(df) < 2:
        return 1
    return 6 if (df.index[-1] - df.index[-2]).total_seconds() / 3600 < 12 else 1


def _ma_on(df: pd.DataFrame, days: int = 200) -> float:
    return float(MAFilter(days * _bars_per_day(df)).positions(df).iloc[-1])


def _trend_w(df: pd.DataFrame) -> float:
    k = _bars_per_day(df)
    return float(trend_vol(df["close"], n=30 * k, ppy=365 * k, mas=(50 * k, 100 * k, 200 * k),
                           moms=(30 * k, 90 * k, 180 * k)).iloc[-1])


SHADOW_PF = {
    "舊 均線(BTC70/ETH30)": _ma_on,
    "D 趨勢分數+波動目標(BTC70/ETH30)": _trend_w,
    "D 同上,調整門檻20%": _trend_w,
}
# 觀察紀錄的版本。2026-11-27 前,K 線改成 4 小時後觀察仍用日線參數 (200 根 = 33 天、波動年化用 365),
# 紀錄無效 -> 版本不符時把舊紀錄搬到 <key>_v<舊版>_invalid 保留,從 100 重新起算。
SHADOW_VER = 2
SHADOW_PF_BAND = {"D 同上,調整門檻20%": 0.20}        # 個別組合的調整門檻 (沒列的用 SHADOW_BAND)
SHADOW_PF_FORK = {"D 同上,調整門檻20%": "D 趨勢分數+波動目標(BTC70/ETH30)"}   # 新組合從既有組合當下的狀態分叉,之後的差異只來自門檻
SLEEVE_BAND = float(os.getenv("SLEEVE_BAND", "0.05"))   # 目標與現值差距小於額度的此比例不調整 (預設 5%;4H 決策建議 0.20,回測用 20%)
SHADOW_BAND = 0.05                                 # 權重變動小於各檔額度的此比例不調整 (歸零除外),壓低換手
COST = SETTINGS.spot_fee + SETTINGS.slippage
PAPER_CAPITAL = float(os.getenv("PAPER_CAPITAL", "10000"))


def _daily_df(sym: str) -> pd.DataFrame:
    iv = getattr(SETTINGS, "timeframe", "D")
    if iv == "240":
        start_ms = int(time.time() * 1000) - 350 * 86_400_000
        df = exchange.fetch_klines(sym, "240", "spot", start_ms=start_ms)
        step = pd.Timedelta(hours=4)
        now = pd.Timestamp.now(tz="UTC")
        return df[df.index + step <= now]
    df = exchange.fetch_klines(sym, "D", "spot", start_ms=int(time.time() * 1000) - 320 * 86_400_000)
    return df[df.index.strftime("%Y-%m-%d") < utc_day()]          # 只用已收盤的日線


def _ticker(sym: str) -> float:
    return float(exchange.HTTP(testnet=False).get_tickers(category="spot", symbol=sym)["result"]["list"][0]["lastPrice"])


def _mark(sym: str) -> float:
    return float(exchange.HTTP(testnet=False).get_tickers(category="linear", symbol=sym)["result"]["list"][0]["markPrice"])


def _funding_since(sym: str, since_ms: int) -> list[tuple[int, float]]:
    now = int(time.time() * 1000)
    r = exchange.HTTP(testnet=False).get_funding_rate_history(category="linear", symbol=sym, startTime=since_ms, endTime=now, limit=200)["result"]["list"]
    return sorted((int(x["fundingRateTimestamp"]), float(x["fundingRate"])) for x in r if int(x["fundingRateTimestamp"]) > since_ms)


def _hourly(sym: str, since_ms: int) -> pd.DataFrame:
    return exchange.fetch_klines(sym, "60", "linear", start_ms=since_ms - 3_600_000)


def _price_at(h: pd.DataFrame, ts_ms: int, fallback: float) -> float:
    if len(h) == 0:
        return fallback
    i = max(0, h.index.searchsorted(pd.Timestamp(ts_ms, unit="ms", tz="UTC"), side="right") - 1)
    return float(h["open"].iloc[i])


def _kv_get(db, k, default):
    r = db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def _kv_set(db, k, v):
    db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))
    db.commit()


def _fa_step(budget, g, paper, perp, fa, day, allowance, halted, marks, now_ms, funding_fn, hourly_fn, actions, unwind=False):
    last = fa.get("last_ts")
    for sym, frac in FA_SYMS.items():
        mark, pos, skip = marks[sym], perp.pos.get(sym), False
        if pos and last:
            h = hourly_fn(sym, last)
            for ts, rate in funding_fn(sym, last):
                perp.accrue(sym, rate, _price_at(h, ts, mark))
            if len(h) and float(h["high"].max()) >= perp.liq_price(pos):
                total = perp.liquidate(sym)
                budget.release(fa["res"].pop(sym, 0.0), total, day)
                spot = fa["spot"].pop(sym, 0.0)
                if spot > 0:
                    g.sell_qty(sym, spot)
                fa["liqs"] += 1; skip = True
                actions.append(f"套利爆倉 {sym} (永續保證金歸零)")
        pos = perp.pos.get(sym)
        if pos and (halted or unwind or mark >= pos.entry * (1 + FA_RECENTER)):
            total = perp.close_short(sym, mark)
            budget.release(fa["res"].pop(sym, 0.0), total, day)
            spot = fa["spot"].pop(sym, 0.0)
            if spot > 0:
                g.sell_qty(sym, spot)
            if not (halted or unwind):
                fa["recenters"] += 1
            actions.append(f"套利{'熔斷平倉' if halted else '停用平倉' if unwind else '重新置中'} {sym}")
        if perp.pos.get(sym) or halted or unwind or skip:
            continue
        cap = allowance * frac
        notional = cap / (1 + 1 / FA_LEV)
        res = cap - notional
        if budget.available(day) < cap - 1e-9 or not budget.reserve(res, day):
            actions.append(f"套利略過 {sym}: 額度不足"); continue
        fill = None
        try:
            fill = g.buy_quote(sym, notional)
            perp.open_short(sym, fill.qty, mark, FA_LEV)
            fa["spot"][sym] = fa["spot"].get(sym, 0.0) + fill.qty
            fa["res"][sym] = res
            actions.append(f"套利開倉 {sym} 現貨{notional:.2f}+空單保證金{perp.pos[sym].margin:.2f}")
        except OrderRejected as e:
            budget.release(res, 0.0, day)
            if fill:
                g.sell_qty(sym, fill.qty)
            actions.append(f"套利拒絕 {sym}: {e}")
    fa["last_ts"] = now_ms


def run_once(db_path: str = SETTINGS.db_path, daily_fn: Callable = _daily_df, price_fn: Callable = _ticker,
             mark_fn: Callable = _mark, funding_fn: Callable = _funding_since, hourly_fn: Callable = _hourly,
             day: str | None = None, force: bool = False, now_ms: int | None = None, fa_enabled: bool | None = None,
             live: bool | None = None, client=None, exit_shield_enabled: bool | None = None,
             slot: str | None = None) -> dict:
    is_explicit_day = day is not None
    day = day or utc_day()
    now_dt = datetime.now(timezone.utc)
    iv = getattr(SETTINGS, "timeframe", "D")
    if slot is None:
        slot = day if (is_explicit_day or iv == "D") else f"{day}-{(now_dt.hour // 4) * 4:02d}"

    budget = DailyBudget(db_path)
    db = budget.db
    db.execute("CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT)")
    db.execute("""CREATE TABLE IF NOT EXISTS snapshots(day TEXT PRIMARY KEY, equity REAL, cash REAL,
                  holdings TEXT, actions TEXT, signals TEXT, halted INTEGER, btc REAL)""")
    live = LIVE if live is None else live
    mode = "live" if live else "paper"
    saved = _kv_get(db, "mode", "paper" if _kv_get(db, "paper", None) is not None else mode)
    if saved != mode:                                        # 模擬與實盤的狀態檔絕不能混用
        raise SystemExit(f"狀態檔是 {saved} 模式,但這次執行是 {mode} 模式;請用各自的狀態檔")
    _kv_set(db, "mode", mode)

    check_key = "last_run_slot" if (not is_explicit_day and iv == "240") else "last_run_day"
    check_val = slot if (not is_explicit_day and iv == "240") else day
    if not force and _kv_get(db, check_key, None) == check_val:
        return {"skipped": True, "day": day, "slot": check_val}

    st = _kv_get(db, "paper", {"cash": PAPER_CAPITAL, "hold": {}, "cost": {}, "peak_px": {}})
    st.setdefault("peak_px", {})
    if not isinstance(st.get("peak_px"), dict):
        st["peak_px"] = {}
    notes = []
    if live:
        from .live import BybitBroker
        paper = BybitBroker(client or exchange.client(), slot, st["hold"], st.get("orders"))
    else:
        paper = PaperBroker(price_fn=price_fn, cash=st["cash"])
        paper._hold = dict(st["hold"])
    g = GuardedBroker(paper, budget, day=day)
    g._cost = dict(st["cost"])
    if live:
        notes = paper.reconcile(g._cost, SLEEVE)             # 補回漏記的成交、以帳戶實際餘額為準

    perp = PerpBook(paper, _kv_get(db, "perp", {}))
    fa = _kv_get(db, "fa", {"last_ts": None, "spot": {}, "res": {}, "liqs": 0, "recenters": 0})
    now_ms = now_ms or int(time.time() * 1000)
    marks = {sym: mark_fn(sym) for sym in FA_SYMS}

    def total_equity():
        return paper.cash + sum(q * price_fn(s) for s, q in paper.holdings().items()) + perp.equity(marks)

    b = budget.open_day(total_equity(), day)
    if live:                                                 # 實盤:佔用額度一律等於目前持倉成本 (對帳後可能有變)
        db.execute("UPDATE daily_budget SET committed=? WHERE day=?", (sum(g._cost.values()), day)); db.commit()
    unreal = sum(q * price_fn(s) - g._cost.get(s, 0.0) for s, q in paper.holdings().items())
    unreal += sum(p.qty * (p.entry - marks[s]) + p.funding for s, p in perp.pos.items())
    halted = budget.check_halt(unreal, day)

    actions, signals = list(notes), {}
    # --- 策略 2:資金費率套利 (現貨多 + 永續空) ---
    fa_on = (FA_ENABLED if fa_enabled is None else fa_enabled) and not live     # 實盤不做套利 (不碰合約)
    if fa_on or perp.pos:                                    # 停用時若仍有持倉,先平掉
        _fa_step(budget, g, paper, perp, fa, day, b["allowance"], halted, marks, now_ms, funding_fn, hourly_fn, actions,
                 unwind=not fa_on)

    # --- 策略 1:趨勢分數 × 波動目標 (D;只動「非套利」的那部分現貨,部位隨分數與波動分批增減) ---
    for sym, frac in SLEEVE.items():
        df = daily_fn(sym)
        step_hours = (df.index[-1] - df.index[-2]).total_seconds() / 3600 if len(df) >= 2 else 24
        is_4h = step_hours < 12
        if is_4h:
            k = 6
            w = float(trend_vol(df["close"], n=30 * k, ppy=365 * k, mas=(50 * k, 100 * k, 200 * k),
                                moms=(30 * k, 90 * k, 180 * k), ema_short=SETTINGS.ema_short).iloc[-1])
        else:
            w = float(trend_vol(df["close"]).iloc[-1])
        w = 0.0 if w != w or halted else w                   # NaN (資料不足) 或熔斷 -> 空手
        signals[sym] = {"on": w > 0, "w": round(w, 3), "close": float(df["close"].iloc[-1]),
                        "sma200": float(df["close"].rolling(200 if not is_4h else 1200).mean().iloc[-1])}
        held = max(0.0, paper.holdings().get(sym, 0.0) - fa["spot"].get(sym, 0.0))
        held = held if held > 1e-9 else 0.0
        px, sleeve = price_fn(sym), b["allowance"] * frac

        diff = sleeve * w - held * px                        # 目標市值 - 目前市值
        band = max(sleeve * SLEEVE_BAND, MIN_ORDER_USDT)     # 差距太小不調整,壓低換手
        try:
            if w == 0 and held > 0:
                actions.append(f"{'熔斷平倉' if halted else '出場'} {sym}"); g.sell_qty(sym, held)
                st["peak_px"].pop(sym, None)
            else:
                # --- 三段式動態退場防護盾 (Hard SL / Break-Even / Trailing TP / RSI) ---
                total_held = paper.holdings().get(sym, 0.0)
                shield_on = SETTINGS.exit_shield_enabled if exit_shield_enabled is None else exit_shield_enabled
                if shield_on and held > 0 and total_held > 1e-9 and g._cost.get(sym, 0.0) > 0:
                    entry_px = g._cost[sym] / total_held
                    peak_px = max(px, st["peak_px"].get(sym, px))
                    st["peak_px"][sym] = peak_px
                    rsi_val = None
                    if len(df) >= 15:
                        d_c = df["close"].diff()
                        g_c = d_c.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
                        l_c = (-d_c.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
                        rsi_val = float((100 - (100 / (1 + g_c / (l_c + 1e-9)))).iloc[-1])
                    decision = evaluate_exit_shield(
                        current_price=px,
                        entry_price=entry_px,
                        peak_price=peak_px,
                        sl_pct=SETTINGS.sl_pct,
                        be_trigger_pct=SETTINGS.be_trigger_pct,
                        be_floor_pct=SETTINGS.be_floor_pct,
                        tp_trigger_pct=SETTINGS.tp_trigger_pct,
                        tp_pullback_pct=SETTINGS.tp_pullback_pct,
                        rsi=rsi_val,
                        rsi_tp_threshold=SETTINGS.rsi_tp_threshold,
                    )
                    if decision.triggered:
                        actions.append(f"出場 {sym} (防護盾: {decision.reason})")
                        g.sell_qty(sym, held)
                        st["peak_px"].pop(sym, None)
                        continue

                if diff <= -band:
                    g.sell_qty(sym, min(held, -diff / px)); actions.append(f"減碼 {sym} {-diff:.2f}USDT (目標 {w:.0%})")
                elif diff >= band:
                    amt = min(diff, budget.available(day))
                    g.buy_quote(sym, amt); actions.append(f"{'加碼' if held else '進場'} {sym} {amt:.2f}USDT (目標 {w:.0%})")
                    st["peak_px"][sym] = px
        except OrderRejected as e:
            actions.append(f"拒絕 {sym}: {e}")

    equity = total_equity() + sleeve_equity(db, mark_fn)     # 紀錄用的權益含永續短線損益 (額度與熔斷的計算不變)
    fa["summary"] = {s: {"qty": p.qty, "entry": p.entry, "liq": PerpBook.liq_price(p), "funding": p.funding,
                         "margin": p.margin, "mark": marks[s]} for s, p in perp.pos.items()}
    _kv_set(db, "paper", {"cash": paper.cash, "hold": paper.holdings(), "cost": g._cost,
                          "orders": getattr(paper, "orders", []), "peak_px": st.get("peak_px", {})})
    _kv_set(db, "perp", perp.dump())
    _kv_set(db, "fa", fa)
    prev_actions = []
    old_row = db.execute("SELECT actions FROM snapshots WHERE day=?", (day,)).fetchone()
    if old_row and old_row[0]:
        try:
            prev_actions = json.loads(old_row[0])
        except Exception:
            pass
    all_actions = list(prev_actions)
    for a in actions:
        if a not in all_actions:
            all_actions.append(a)
    db.execute("INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?,?,?,?,?)",
               (day, equity, paper.cash, json.dumps(paper.holdings()), json.dumps(all_actions, ensure_ascii=False),
                json.dumps(signals), int(budget.get(day)["halted"]), price_fn("BTCUSDT")))
    _reset_shadow_if_stale(db)
    shadow = _update_shadow(db, daily_fn, price_fn, day)
    shadow_pf = _update_shadow_pf(db, daily_fn, price_fn, day)
    _kv_set(db, "last_run_day", day)
    _kv_set(db, "last_run_slot", slot)
    return {"mode": mode, "shadow": shadow, "shadow_pf": shadow_pf, "day": day, "slot": slot, "equity": equity, "allowance": b["allowance"], "signals": signals, "actions": actions,
            "halted": bool(budget.get(day)["halted"])}


def _reset_shadow_if_stale(db) -> None:
    ver = _kv_get(db, "shadow_ver", 1)
    if ver == SHADOW_VER:
        return
    for key in ("shadow", "shadow_pf"):
        old = _kv_get(db, key, None)
        if old:
            _kv_set(db, f"{key}_v{ver}_invalid", old)
        db.execute("DELETE FROM kv WHERE k=?", (key,))
    _kv_set(db, "shadow_ver", SHADOW_VER)


def _update_shadow(db, daily_fn, price_fn, day: str | None = None) -> dict:
    sh = _kv_get(db, "shadow", {})
    for sym in SHADOW:
        try:
            on = bool(_ma_on(daily_fn(sym)))
            p = price_fn(sym)
        except Exception as e:                      # 觀察用,任何一檔出錯都不能影響主流程
            sh.setdefault(sym, {})["error"] = str(e)[:80]
            continue
        s = sh.get(sym) or {"idx": 100.0, "on": False, "last": p, "start": p, "since": day}
        s.pop("error", None)
        if s["on"]:
            s["idx"] *= p / s["last"]               # 持有期間的報酬
        if on != s["on"]:
            s["idx"] *= 1 - COST                    # 進出場成本
        s["on"], s["last"] = on, p
        sh[sym] = s
    _kv_set(db, "shadow", sh)
    return {k: {"on": v.get("on"), "idx": round(v.get("idx", 0), 2)} for k, v in sh.items()}


def _update_shadow_pf(db, daily_fn, price_fn, day: str | None = None) -> dict:
    """觀察組合:先用舊權重結算上次以來的報酬,再依最新日線算新目標權重 (扣換手成本)。"""
    pf = _kv_get(db, "shadow_pf", {})
    try:
        px = {s: price_fn(s) for s in SHADOW_MIX}
        dfs = {s: daily_fn(s) for s in SHADOW_MIX}
    except Exception as e:                          # 觀察用,出錯不能影響主流程
        pf["error"] = str(e)[:80]
        _kv_set(db, "shadow_pf", pf)
        return {"error": pf["error"]}
    pf.pop("error", None)
    for old, new_name in SHADOW_PF_RENAMED.items():
        if old in pf:
            pf.setdefault(new_name, pf[old]); del pf[old]
    for name, src in SHADOW_PF_FORK.items():
        if name not in pf and src in pf:
            pf[name] = json.loads(json.dumps(pf[src]))
    for name, fn in SHADOW_PF.items():
        band = SHADOW_PF_BAND.get(name, SHADOW_BAND)
        s = pf.get(name) or {"idx": 100.0, "w": {}, "last": px, "turn": 0.0, "days": 0, "since": day}
        s["idx"] *= 1 + sum(w * (px[k] / s["last"][k] - 1) for k, w in s["w"].items())
        new = {}
        for k, mix in SHADOW_MIX.items():
            tgt = fn(dfs[k]) * mix
            tgt = 0.0 if tgt != tgt else tgt        # NaN (資料不足) 視為空手
            cur = s["w"].get(k, 0.0)
            new[k] = tgt if abs(tgt - cur) >= band * mix or (tgt == 0 and cur != 0) else cur   # 門檻按各檔額度計 (與實盤一致)
        turn = sum(abs(new[k] - s["w"].get(k, 0.0)) for k in new)
        s["idx"] *= 1 - turn * COST
        s.update(w=new, last=px, turn=s["turn"] + turn, days=s["days"] + 1)
        pf[name] = s
    _kv_set(db, "shadow_pf", pf)
    return {k: {"idx": round(v["idx"], 2), "w": {a: round(b, 3) for a, b in v["w"].items()}} for k, v in pf.items() if k != "error"}


def _days_since(start: str | None, today: str) -> int | None:
    if not start:
        return None
    return (pd.Timestamp(today) - pd.Timestamp(start[:10])).days + 1


def report(db_path: str = SETTINGS.db_path) -> str:
    db = DailyBudget(db_path).db
    try:
        rows = db.execute("SELECT day,equity,btc,actions,halted FROM snapshots ORDER BY day").fetchall()
    except Exception:
        return "尚無紀錄"
    if not rows:
        return "尚無紀錄"
    e0, b0 = rows[0][1], rows[0][2]
    peak, out = e0, [f"{'日期':10s} {'權益':>10s} {'累計':>8s} {'BTC持有':>8s} {'回撤':>7s}  動作"]
    for d, e, b, a, h in rows:
        peak = max(peak, e)
        out.append(f"{d:10s} {e:10.2f} {e/e0-1:+8.2%} {b/b0-1:+8.2%} {e/peak-1:7.2%}  {'; '.join(json.loads(a)) or '-'}{' [熔斷]' if h else ''}")
    fa = _kv_get(db, "fa", {})
    if fa.get("summary") is not None:
        out += ["", f"資金費率套利 ({'啟用' if FA_ENABLED else '選配,已停用'};現貨多+永續空 {FA_LEV}x) | 爆倉 {fa.get('liqs', 0)} 次,重新置中 {fa.get('recenters', 0)} 次",
                f"{'幣':8s} {'進場價':>10s} {'現價':>10s} {'強平價':>10s} {'距強平':>7s} {'累計funding':>11s}"]
        for sym, v in fa["summary"].items():
            out.append(f"{sym:8s} {v['entry']:10.2f} {v['mark']:10.2f} {v['liq']:10.2f} {v['liq']/v['mark']-1:+7.1%} {v['funding']:+11.4f}")
        if not fa["summary"]:
            out.append("  (目前無套利持倉)")
    sh = _kv_get(db, "shadow", {})
    if sh:
        since = next((v["since"] for v in sh.values() if v.get("since")), None)
        out += ["", f"觀察模式 (不下單,虛擬績效;起算 100{f',自 {since} 起' if since else ''})", f"{'幣':8s} {'訊號':>4s} {'均線策略':>9s} {'直接持有':>9s}"]
        for sym, v in sh.items():
            if "idx" in v:
                out.append(f"{sym:8s} {'持有' if v['on'] else '空手':>4s} {v['idx']/100-1:+9.2%} {v['last']/v['start']-1:+9.2%}")
            else:
                out.append(f"{sym:8s} 取得資料失敗: {v.get('error')}")
    pf = _kv_get(db, "shadow_pf", {})
    if pf:
        since = next((v["since"] for k, v in pf.items() if k != "error" and v.get("since")), None)
        out += ["", f"觀察組合 (不下單;整份虛擬資金起算 100,同日起跑並排比較{f',自 {since} 起' if since else ''})",
                f"{'組合':30s} {'績效':>8s} {'BTC權重':>7s} {'ETH權重':>7s} {'累計換手':>8s} {'天數':>4s}"]
        if "error" in pf:
            out.append(f"  取得資料失敗: {pf['error']}")
        for name, v in pf.items():
            if name != "error":
                w = v["w"]
                days = _days_since(v.get("since"), rows[-1][0]) or v["days"]
                out.append(f"{name:30s} {v['idx']/100-1:+8.2%} {w.get('BTCUSDT', 0):7.0%} {w.get('ETHUSDT', 0):7.0%} {v['turn']:8.2f} {days:4d}")
    from .perp_sleeve import summary
    out += summary(db)
    return "\n".join(out)


PERF_WINDOWS = (0, 7, 20, 30, 60, 90, 180, 360)


def perf(db_path: str = SETTINGS.db_path, price_fn: Callable = _ticker, windows=PERF_WINDOWS, today: str | None = None) -> str:
    """各期間的績效 (金額與百分比)、同期直接持有 BTC 的報酬、期間內最大回撤。
    終點 = 現在 (用即時價格重估持倉);起點 = N 天前那次執行的紀錄。紀錄不到 N 天就從第一筆算起並標示。
    注意:權益含現金,期間內若有入金/出金會被算成損益。"""
    db = DailyBudget(db_path).db
    try:
        rows = db.execute("SELECT day,equity,btc FROM snapshots ORDER BY day").fetchall()
    except Exception:
        rows = []
    if not rows:
        return "尚無紀錄"
    st = _kv_get(db, "paper", {"cash": 0.0, "hold": {}})
    perp = _kv_get(db, "perp", {})
    eq_now = st["cash"] + sum(q * price_fn(s) for s, q in st["hold"].items())
    eq_now += sum(p["margin"] for p in perp.values())                                # 套利保證金 (停用時為空)
    eq_now += sleeve_equity(db, price_fn)                                            # 永續短線損益 (用現貨價近似標記價)
    today = today or utc_day()
    pts = [(d, e, b) for d, e, b in rows] + [("現在", eq_now, price_fn("BTCUSDT"))]
    mode = _kv_get(db, "mode", "paper")
    out = [f"{'實盤' if mode == 'live' else '模擬'}績效 | 起始 {rows[0][0]} 權益 {rows[0][1]:.2f} | 現在 {eq_now:.2f} | 已有 {len(rows)} 天紀錄",
           f"{'期間':6s} {'起點':10s} {'損益':>10s} {'報酬':>8s} {'BTC持有':>8s} {'期間回撤':>8s}  備註"]
    t = pd.Timestamp(today)
    for n in windows:
        cut = (t - pd.Timedelta(days=n)).strftime("%Y-%m-%d")
        idx = [i for i, r in enumerate(rows) if r[0] <= cut]
        i = idx[-1] if idx else 0
        d0, e0, b0 = rows[i]
        seg = [e for _, e, _ in pts[i:]]
        peak, dd = seg[0], 0.0
        for e in seg:
            peak = max(peak, e); dd = min(dd, e / peak - 1)
        note = "" if idx else f"紀錄不足 {n} 天,從第一筆算起"
        if n == 0 and d0 != today:
            note = f"今天尚未執行,從 {d0} 算起"
        out.append(f"{'當日' if n == 0 else f'{n}日':6s} {d0:10s} {eq_now - e0:+10.2f} {eq_now / e0 - 1:+8.2%} "
                   f"{pts[-1][2] / b0 - 1:+8.2%} {dd:8.2%}  {note}")
    return "\n".join(out)
