from dataclasses import dataclass
import os

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    env: str = os.getenv("BYBIT_ENV", "demo")
    api_key: str = os.getenv("BYBIT_API_KEY", "")
    api_secret: str = os.getenv("BYBIT_API_SECRET", "")
    rsa_key_path: str = os.getenv("BYBIT_RSA_KEY_PATH", "")   # 有填就用 RSA 簽名

    # 資金規則
    budget_pct: float = float(os.getenv("BUDGET_PCT", "0.05"))   # 每日可動用資金 = 總權益 * 5% (實盤 Job 用環境變數調整)
    halt_loss_pct: float = 0.30     # 單日虧損達「可用資金」的 30% 即熔斷
    perp_max_leverage: int = 2      # 永續槓桿上限

    # 週期與多時間架構設定
    timeframe: str = os.getenv("TIMEFRAME", "D")                 # 現貨決策週期:"D" (預設,每天一次) 或 "240"
    ema_short: bool = os.getenv("EMA_SHORT", "0") == "1"         # 4H EMA21/50 短期趨勢濾網

    # 三段式動態退場防護盾設定
    exit_shield_enabled: bool = os.getenv("EXIT_SHIELD", "0") == "1"   # 預設關閉:回測換手暴增、報酬轉負
    sl_pct: float = float(os.getenv("SL_PCT", "3.0"))            # 硬停損 %
    tp_trigger_pct: float = float(os.getenv("TP_TRIGGER_PCT", "4.5")) # 移動停利啟動 %
    tp_pullback_pct: float = float(os.getenv("TP_PULLBACK_PCT", "1.5")) # 移動停利回撤 %
    be_trigger_pct: float = float(os.getenv("BE_TRIGGER_PCT", "2.5"))  # 保本啟動 %
    be_floor_pct: float = float(os.getenv("BE_FLOOR_PCT", "0.3"))     # 保本底線 %
    rsi_tp_threshold: float = float(os.getenv("RSI_TP_THRESHOLD", "80.0")) # RSI 超買停利線

    # 交易成本 (回測用)
    spot_fee: float = 0.001
    slippage: float = 0.0005

    db_path: str = os.getenv("DB_PATH", "data/bot.db")


SETTINGS = Settings()

# 現貨白名單 (第一層,流動性高;排除迷因幣)。實盤另需每日檢查 30 日成交額中位數。
SPOT_WHITELIST = frozenset({"BTCUSDT", "ETHUSDT", "LINKUSDT", "XRPUSDT", "SOLUSDT", "ENAUSDT", "AAVEUSDT"})
MIN_ORDER_USDT = 5.0
