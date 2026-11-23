"""下單介面。策略只依賴 Broker;PaperBroker 在本機模擬,BybitBroker 之後再接真實 API。
GuardedBroker 在任何 Broker 外層強制:白名單、最小下單額、每日額度、熔斷。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

from .budget import DailyBudget
from .config import MIN_ORDER_USDT, SETTINGS, SPOT_WHITELIST


class OrderRejected(Exception):
    pass


@dataclass
class Fill:
    symbol: str
    side: str
    qty: float
    price: float
    fee: float


class Broker(ABC):
    @abstractmethod
    def price(self, symbol: str) -> float: ...
    @abstractmethod
    def buy_quote(self, symbol: str, quote_usdt: float) -> Fill: ...
    @abstractmethod
    def sell_qty(self, symbol: str, qty: float) -> Fill: ...
    @abstractmethod
    def holdings(self) -> dict[str, float]: ...


@dataclass
class PaperBroker(Broker):
    """用真實(或測試注入)價格撮合的模擬現貨帳戶。市價單吃滑價,收 taker 手續費。"""
    price_fn: Callable[[str], float]
    cash: float = 0.0
    fee: float = SETTINGS.spot_fee
    slip: float = SETTINGS.slippage
    _hold: dict = field(default_factory=dict)

    def price(self, symbol): return self.price_fn(symbol)

    def buy_quote(self, symbol, quote_usdt):
        if quote_usdt > self.cash + 1e-9:
            raise OrderRejected("模擬帳戶現金不足")
        px = self.price(symbol) * (1 + self.slip)
        fee = quote_usdt * self.fee
        qty = (quote_usdt - fee) / px
        self.cash -= quote_usdt
        self._hold[symbol] = self._hold.get(symbol, 0.0) + qty
        return Fill(symbol, "Buy", qty, px, fee)

    def sell_qty(self, symbol, qty):
        if qty > self._hold.get(symbol, 0.0) + 1e-12:
            raise OrderRejected("持倉不足")
        px = self.price(symbol) * (1 - self.slip)
        gross = qty * px
        fee = gross * self.fee
        self.cash += gross - fee
        self._hold[symbol] -= qty
        return Fill(symbol, "Sell", qty, px, fee)

    def holdings(self): return {k: v for k, v in self._hold.items() if v > 1e-12}


class GuardedBroker(Broker):
    def __init__(self, inner: Broker, budget: DailyBudget, whitelist=SPOT_WHITELIST, day: str | None = None):
        self.inner, self.budget, self.whitelist, self.day = inner, budget, whitelist, day
        self._cost: dict[str, float] = {}      # 每個標的目前佔用的額度 (USDT)

    def price(self, symbol): return self.inner.price(symbol)
    def holdings(self): return self.inner.holdings()

    def buy_quote(self, symbol, quote_usdt):
        if symbol not in self.whitelist:
            raise OrderRejected(f"{symbol} 不在白名單")
        if quote_usdt < MIN_ORDER_USDT:
            raise OrderRejected(f"下單額 {quote_usdt:.2f} 低於最小下單額 {MIN_ORDER_USDT}")
        if not self.budget.reserve(quote_usdt, self.day):
            raise OrderRejected("超出今日額度或已熔斷")
        try:
            fill = self.inner.buy_quote(symbol, quote_usdt)
        except Exception:
            self.budget.release(quote_usdt, 0.0, self.day)     # 下單失敗,退還額度
            raise
        self._cost[symbol] = self._cost.get(symbol, 0.0) + quote_usdt
        return fill

    def sell_qty(self, symbol, qty):
        """賣出一律允許 (出場不應被擋),並釋放額度、記錄已實現損益。"""
        held = self.inner.holdings().get(symbol, 0.0)
        fill = self.inner.sell_qty(symbol, qty)
        cost_part = self._cost.get(symbol, 0.0) * (qty / held if held else 1.0)
        proceeds = fill.qty * fill.price - fill.fee
        self._cost[symbol] = self._cost.get(symbol, 0.0) - cost_part
        self.budget.release(cost_part, proceeds - cost_part, self.day)
        return fill
