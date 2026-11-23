"""模擬永續合約空單簿 (逐倉、單向)。現金與 PaperBroker 共用。
空單:漲價虧損、跌價獲利;funding>0 時空頭收費。保證金 = 名目/槓桿。"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .broker import OrderRejected, PaperBroker
from .config import SETTINGS

PERP_FEE = 0.00055      # taker
MMR = 0.005             # 維持保證金率 (簡化)


@dataclass
class PerpPos:
    qty: float
    entry: float
    margin: float
    lev: float
    funding: float = 0.0     # 累計已收 funding (已進現金)


class PerpBook:
    def __init__(self, paper: PaperBroker, state: dict | None = None):
        self.paper = paper
        self.pos: dict[str, PerpPos] = {k: PerpPos(**v) for k, v in (state or {}).items()}

    def dump(self) -> dict:
        return {k: asdict(v) for k, v in self.pos.items()}

    @staticmethod
    def liq_price(p: PerpPos) -> float:
        return p.entry * (1 + 1 / p.lev) / (1 + MMR)

    def open_short(self, sym: str, qty: float, mark: float, lev: float) -> PerpPos:
        px = mark * (1 - SETTINGS.slippage)
        notional = qty * px
        margin, fee = notional / lev, notional * PERP_FEE
        if self.paper.cash < margin + fee:
            raise OrderRejected("模擬帳戶現金不足以支付保證金")
        self.paper.cash -= margin + fee
        self.pos[sym] = PerpPos(qty, px, margin, lev)
        return self.pos[sym]

    def accrue(self, sym: str, rate: float, px: float) -> float:
        p = self.pos[sym]
        amt = p.qty * px * rate
        self.paper.cash += amt
        p.funding += amt
        return amt

    def close_short(self, sym: str, mark: float) -> float:
        """回傳這筆永續的總損益 (價差 - 手續費 + 累計 funding)。"""
        p = self.pos.pop(sym)
        px = mark * (1 + SETTINGS.slippage)
        pnl, fee = p.qty * (p.entry - px), p.qty * px * PERP_FEE
        self.paper.cash += p.margin + pnl - fee
        return pnl - fee + p.funding

    def liquidate(self, sym: str) -> float:
        """被強平:保證金歸零。回傳總損益 (= -保證金 + 累計 funding)。"""
        p = self.pos.pop(sym)
        return -p.margin + p.funding

    def equity(self, marks: dict[str, float]) -> float:
        return sum(p.margin + p.qty * (p.entry - marks[s]) for s, p in self.pos.items())
