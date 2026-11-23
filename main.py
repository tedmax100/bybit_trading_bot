"""用法:
  python main.py backtest [SYMBOL] [DAYS]   現貨趨勢策略回測 (公開資料,不需 key)
  python main.py gate [SYMBOL]              今天永續閘門是否開放
  python main.py run [--force]               執行今日模擬交易 (每個 UTC 日只執行一次)
  python main.py report                     資金曲線 (每天一列)
  python main.py perf                       當日/7/20/30/60/90/180/360 日績效 (DB_PATH 指定狀態檔)
  python main.py budget                     讀帳戶權益並結算今日額度 (需 API key)
"""
import sys

from bot import exchange
from bot.backtest import backtest
from bot.budget import DailyBudget
from bot.gate import perp_gate
from bot.strategies.trend_spot import TrendSpot


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "backtest"
    sym = argv[2] if len(argv) > 2 else "BTCUSDT"
    if cmd == "backtest":
        days = int(argv[3]) if len(argv) > 3 else 730
        end = int(__import__("time").time() * 1000)
        df = exchange.fetch_klines(sym, "240", "spot", start_ms=end - days * 86_400_000)
        for k, v in backtest(df, TrendSpot()).items():
            print(f"{k:20s} {v:.4f}" if isinstance(v, float) else f"{k:20s} {v}")
    elif cmd == "gate":
        df = exchange.fetch_klines(sym, "60", "linear")
        df = exchange.fetch_klines(sym, "60", "linear", start_ms=int(df.index[-1].timestamp() * 1000) - 14 * 86_400_000)
        g = perp_gate(df, exchange.funding_rate(sym))
        print("永續閘門:", "開放" if g.open else "關閉", *g.reasons, sep="\n  ")
    elif cmd == "budget":
        b = DailyBudget().open_day(exchange.total_equity(exchange.client()))
        print(b)
    elif cmd == "run":
        from bot.runner import run_once
        print(run_once(force="--force" in argv))
        from bot import perp_sleeve
        if perp_sleeve.ENABLED:
            print(perp_sleeve.perp_step(force="--force" in argv))
    elif cmd == "perf":
        from bot.runner import perf
        print(perf())
    elif cmd == "report":
        from bot.runner import report
        print(report())
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
