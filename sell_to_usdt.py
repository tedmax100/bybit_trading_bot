"""把統一交易帳戶裡的非穩定幣現貨,用市價單賣成 USDT (實盤 mainnet)。

預設只列出會做什麼,不下單;加 --execute 才真的賣。
  BYBIT_ENV=mainnet .venv/bin/python sell_to_usdt.py              # 只看
  BYBIT_ENV=mainnet .venv/bin/python sell_to_usdt.py --execute    # 真的賣
  --include-usdc   連 USDC 也換成 USDT (預設保留)
  --only BTC,ETH   只處理指定幣別

低於交易所最小下單量/金額的零頭賣不掉,會標示「略過」。
"""
from __future__ import annotations

import sys
from decimal import ROUND_DOWN, Decimal

from bot import exchange
from bot.config import SETTINGS

KEEP = {"USDT"}


def floor_to(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(rounding=ROUND_DOWN) * step


def plan(c, include_usdc: bool, only: set[str] | None) -> list[dict]:
    keep = KEEP if include_usdc else KEEP | {"USDC"}
    coins = c.get_wallet_balance(accountType="UNIFIED")["result"]["list"][0]["coin"]
    rows = []
    for co in coins:
        coin, bal = co["coin"], Decimal(co.get("walletBalance") or "0")
        locked = Decimal(co.get("locked") or "0")
        free = bal - locked
        usd = Decimal(co.get("usdValue") or "0")
        row = dict(coin=coin, balance=bal, usd=usd, qty=Decimal(0), symbol=None, note="")
        rows.append(row)
        if coin in keep:
            row["note"] = "保留"
            continue
        if only and coin not in only:
            row["note"] = "不在 --only 名單"
            continue
        if free <= 0:
            row["note"] = "無可用餘額"
            continue
        symbol = f"{coin}USDT"
        info = c.get_instruments_info(category="spot", symbol=symbol)["result"]["list"]
        if not info or info[0].get("status") != "Trading":
            row["note"] = f"沒有可交易的 {symbol} 現貨"
            continue
        lot = info[0]["lotSizeFilter"]
        qty = floor_to(free, Decimal(lot["basePrecision"]))
        price = Decimal(c.get_tickers(category="spot", symbol=symbol)["result"]["list"][0]["bid1Price"])
        if qty < Decimal(lot["minOrderQty"]) or qty * price < Decimal(lot["minOrderAmt"]):
            row["note"] = f"略過:低於最小下單 (量 {lot['minOrderQty']} / 金額 {lot['minOrderAmt']} USDT)"
            continue
        row.update(symbol=symbol, qty=qty, note=f"市價賣出 ≈ {qty * price:.2f} USDT")
    return rows


def main(argv: list[str]) -> None:
    execute = "--execute" in argv
    only = None
    if "--only" in argv:
        only = {s.strip().upper() for s in argv[argv.index("--only") + 1].split(",")}
    print(f"環境: {SETTINGS.env}   模式: {'實際下單' if execute else '只列出,不下單'}")
    c = exchange.client()
    rows = plan(c, "--include-usdc" in argv, only)
    for r in rows:
        print(f"  {r['coin']:<8} 餘額 {r['balance']:<18} ≈ {r['usd']:.2f} USD   {r['note']}")
    todo = [r for r in rows if r["symbol"]]
    if not todo:
        print("沒有可以賣的部位。")
        return
    if not execute:
        print("以上為預覽;加 --execute 才會下單。")
        return
    for r in todo:
        res = c.place_order(category="spot", symbol=r["symbol"], side="Sell", orderType="Market", qty=str(r["qty"]))
        print(f"  已送出 {r['symbol']} 賣 {r['qty']}: orderId={res['result'].get('orderId')}")
    usdt = [x for x in c.get_wallet_balance(accountType="UNIFIED")["result"]["list"][0]["coin"] if x["coin"] == "USDT"]
    print("賣出後 USDT 餘額:", usdt[0]["walletBalance"] if usdt else "0")


if __name__ == "__main__":
    main(sys.argv[1:])
