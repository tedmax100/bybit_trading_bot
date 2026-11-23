"""Cloud Run Job 入口:容器每次都是全新的,所以執行前從 Cloud Storage 取回 bot.db,跑完再傳回去。

環境變數:
  STATE_BUCKET  存放狀態的 bucket 名稱 (必填)
  STATE_OBJECT  物件名稱,預設 bot.db
  STATE_INIT=1  bucket 裡還沒有狀態檔時,允許從頭開始 (預設不允許,避免設定錯誤時悄悄重開一個新帳戶)

上傳時帶 generation 條件:若執行期間狀態檔被別的執行改過,這次上傳會失敗而不是覆蓋對方。
現貨當天已執行過、永續這根 4 小時 K 線也已決策過 (都 skipped) 就不上傳。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from bot.config import SETTINGS


def pull(blob, db: Path, allow_init: bool) -> int:
    """下載狀態檔,回傳它的 generation;不存在時回傳 0 (代表「只有在仍不存在時才可建立」)。"""
    db.parent.mkdir(parents=True, exist_ok=True)
    if not blob.exists():
        if not allow_init:
            raise SystemExit(f"找不到狀態檔 gs://{blob.bucket.name}/{blob.name};若要從頭開始請設 STATE_INIT=1")
        print("找不到既有狀態,從頭開始 (STATE_INIT=1)")
        return 0
    blob.reload()
    gen = blob.generation
    blob.download_to_filename(str(db), if_generation_match=gen)
    return gen


def push(blob, db: Path, gen: int) -> None:
    blob.upload_from_filename(str(db), if_generation_match=gen)


def main(argv, blob=None) -> dict:
    if blob is None:
        from google.cloud import storage
        blob = storage.Client().bucket(os.environ["STATE_BUCKET"]).blob(os.getenv("STATE_OBJECT", "bot.db"))
    db = Path(SETTINGS.db_path)
    gen = pull(blob, db, os.getenv("STATE_INIT") == "1")

    from bot.runner import report, run_once
    res = run_once(force="--force" in argv)
    print(res, flush=True)
    from bot import perp_sleeve                # 永續短線:每 4 小時一次,與現貨 (每天一次) 各自判斷是否已執行
    perr = None
    try:
        pres = perp_sleeve.perp_step() if perp_sleeve.ENABLED else {"skipped": True}
    except Exception as e:                   # 永續出錯不能讓現貨已成交的紀錄 (與永續已存的部分) 遺失:照樣上傳,再以失敗結束
        pres, perr = {"error": str(e)[:300]}, e
    print(pres, flush=True)
    if res.get("skipped") and pres.get("skipped"):
        return res
    push(blob, db, gen)                      # 失敗會丟例外 -> Job 以失敗結束,下一次補跑會重做當天
    print(report(), flush=True)
    if perr is not None:
        raise SystemExit(f"永續步驟失敗: {perr}")
    return res


if __name__ == "__main__":
    main(sys.argv)
