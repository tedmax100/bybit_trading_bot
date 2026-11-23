"""每日資金額度:每天 UTC 00:00 以當時總權益 * 5% 重算,所有策略共用。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import SETTINGS


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class DailyBudget:
    def __init__(self, db_path: str = SETTINGS.db_path,
                 budget_pct: float = SETTINGS.budget_pct,
                 halt_loss_pct: float = SETTINGS.halt_loss_pct):
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.budget_pct = budget_pct
        self.halt_loss_pct = halt_loss_pct
        self.db.execute("""CREATE TABLE IF NOT EXISTS daily_budget(
            day TEXT PRIMARY KEY, equity REAL, allowance REAL,
            committed REAL DEFAULT 0, realized_pnl REAL DEFAULT 0, halted INTEGER DEFAULT 0)""")
        self.db.commit()

    # ---- 結算 / 開日 ----
    def open_day(self, equity: float, day: str | None = None) -> dict:
        """每天結算一次。同一天重複呼叫不會重算 (避免盤中被權益波動改掉額度)。
        前一天尚未平倉的佔用資金 (carry) 會延續到新的一天。"""
        day = day or utc_day()
        if self.get(day) is None:
            carry = self.db.execute(
                "SELECT committed FROM daily_budget ORDER BY day DESC LIMIT 1").fetchone()
            carry_amt = carry[0] if carry else 0.0
            self.db.execute(
                "INSERT INTO daily_budget(day,equity,allowance,committed) VALUES(?,?,?,?)",
                (day, equity, equity * self.budget_pct, carry_amt))
            self.db.commit()
        return self.get(day)

    def get(self, day: str | None = None) -> dict | None:
        r = self.db.execute(
            "SELECT day,equity,allowance,committed,realized_pnl,halted FROM daily_budget WHERE day=?",
            (day or utc_day(),)).fetchone()
        if not r:
            return None
        return dict(zip(("day", "equity", "allowance", "committed", "realized_pnl", "halted"), r))

    # ---- 額度查詢 ----
    def available(self, day: str | None = None) -> float:
        b = self.get(day)
        if b is None or b["halted"]:
            return 0.0
        return max(0.0, b["allowance"] - b["committed"])

    def reserve(self, amount: float, day: str | None = None) -> bool:
        """策略下單前先申請額度;不足就拒絕 (不部分成交)。"""
        if amount <= 0 or amount > self.available(day) + 1e-9:
            return False
        self.db.execute("UPDATE daily_budget SET committed=committed+? WHERE day=?",
                        (amount, day or utc_day()))
        self.db.commit()
        return True

    def release(self, amount: float, pnl: float, day: str | None = None) -> None:
        """平倉後釋放佔用資金並記錄已實現損益。"""
        d = day or utc_day()
        self.db.execute(
            "UPDATE daily_budget SET committed=MAX(0,committed-?), realized_pnl=realized_pnl+? WHERE day=?",
            (amount, pnl, d))
        self.db.commit()
        self.check_halt(0.0, d)

    # ---- 熔斷 ----
    def check_halt(self, unrealized_pnl: float = 0.0, day: str | None = None) -> bool:
        """已實現 + 未實現虧損 >= 可用資金 * 30% -> 熔斷。回傳是否已熔斷。"""
        d = day or utc_day()
        b = self.get(d)
        if b is None:
            return False
        if b["halted"]:
            return True
        if b["realized_pnl"] + unrealized_pnl <= -b["allowance"] * self.halt_loss_pct:
            self.db.execute("UPDATE daily_budget SET halted=1 WHERE day=?", (d,))
            self.db.commit()
            return True
        return False
