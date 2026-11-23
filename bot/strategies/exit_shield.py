"""三段式動態退場防護盾 (Three-Stage Exit Shield):
參考 QuantitativeTrading 的風控哲學與實務經驗，為部位提供三層主動防護：
1. 第一層：自適應硬停損 (Hard Stop-Loss) - 虧損達臨界值立即市價平倉截斷風險，拒絕死扛。
2. 第二層：保本階梯鎖利 (Break-Even Ratchet) - 浮盈達門檻後將停損點拉至成本微賺線，確保不倒虧。
3. 第三層：動態移動停利 (Trailing Take-Profit) - 鎖定波段頂部回撤，隨行情上漲動態推升停利線。
4. 極端 RSI 耗盡停利 (RSI Exhaustion Exit) - 超買時主動落袋為安。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExitDecision:
    triggered: bool
    action: str = ""       # "STOP_LOSS", "BREAK_EVEN", "TRAILING_TP", "RSI_TP"
    reason: str = ""
    pnl_pct: float = 0.0
    peak_pct: float = 0.0


def evaluate_exit_shield(
    current_price: float,
    entry_price: float,
    peak_price: float,
    sl_pct: float = 3.0,          # 停損門檻: -3.0%
    be_trigger_pct: float = 2.5,  # 保本啟動浮盈: +2.5%
    be_floor_pct: float = 0.3,    # 保本保底: +0.3% (覆蓋手續費與滑價)
    tp_trigger_pct: float = 4.5,  # 移動停利啟動浮盈: +4.5%
    tp_pullback_pct: float = 1.5, # 移動停利回撤門檻: 1.5%
    rsi: float | None = None,
    rsi_tp_threshold: float = 80.0,
) -> ExitDecision:
    """評估持倉是否應觸發動態防護退場。

    回傳 ExitDecision，若 triggered=True 則應全額出場。
    """
    if entry_price <= 0 or current_price <= 0:
        return ExitDecision(triggered=False)

    pnl_pct = (current_price - entry_price) / entry_price * 100.0
    peak_px = max(peak_price, current_price, entry_price)
    peak_pct = (peak_px - entry_price) / entry_price * 100.0
    pullback = peak_pct - pnl_pct

    # 1. 第一層: 硬停損 (Hard Stop Loss)
    if pnl_pct <= -sl_pct:
        return ExitDecision(
            triggered=True,
            action="STOP_LOSS",
            reason=f"自適應停損觸發 (虧損 {pnl_pct:.2f}% <= -{sl_pct:.2f}%)",
            pnl_pct=pnl_pct,
            peak_pct=peak_pct,
        )

    # 4. 極端超買獲利了結 (RSI Exhaustion TP: 需浮盈已達保本線且 RSI 極度過熱)
    if rsi is not None and rsi >= rsi_tp_threshold and pnl_pct >= be_trigger_pct:
        return ExitDecision(
            triggered=True,
            action="RSI_TP",
            reason=f"RSI 極端超買停利 (RSI={rsi:.1f} >= {rsi_tp_threshold}, 獲利 +{pnl_pct:.2f}%)",
            pnl_pct=pnl_pct,
            peak_pct=peak_pct,
        )

    # 3. 第三層: 動態移動停利 (Trailing Take Profit)
    if peak_pct >= tp_trigger_pct and pullback >= tp_pullback_pct:
        return ExitDecision(
            triggered=True,
            action="TRAILING_TP",
            reason=f"動態移動停利觸發 (最高 +{peak_pct:.2f}%, 回撤 {pullback:.2f}%, 鎖定 +{pnl_pct:.2f}%)",
            pnl_pct=pnl_pct,
            peak_pct=peak_pct,
        )

    # 2. 第二層: 階梯保本鎖利 (Break-Even Ratchet)
    if peak_pct >= be_trigger_pct and pnl_pct <= be_floor_pct:
        return ExitDecision(
            triggered=True,
            action="BREAK_EVEN",
            reason=f"保本鎖利觸發 (最高浮盈曾達 +{peak_pct:.2f}%, 回落至 +{pnl_pct:.2f}%)",
            pnl_pct=pnl_pct,
            peak_pct=peak_pct,
        )

    return ExitDecision(triggered=False, pnl_pct=pnl_pct, peak_pct=peak_pct)
