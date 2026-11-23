#!/usr/bin/env bash
# 第 3 步:把模擬交易部署到 GCP 台灣區域 (Cloud Run Job + Cloud Scheduler)。
# 2026-11-10 已實際部署過 (專案 bybit-bot-909483)。重建時請先 `gcloud auth login`,確認變數後逐段執行,不要一次整份跑。
# 注意:新專案剛啟用 API 後的一兩分鐘內可能回 PERMISSION_DENIED,等一下重試即可;請用 bash 執行 (zsh 會把 $JOB:run 展開錯)。
# 用法: PROJECT=你的專案ID bash deploy/gcp.sh
set -euo pipefail

PROJECT="${PROJECT:?請設定 PROJECT=你的 GCP 專案 ID}"
REGION=asia-east1                                  # 台灣 (彰化)
BUCKET="${BUCKET:-$PROJECT-bybit-bot-state}"       # bucket 名稱全球唯一
REPO=bybit-bot
JOB=bybit-bot-daily
SA=bybit-bot-runner
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/bot:latest"
SA_EMAIL="$SA@$PROJECT.iam.gserviceaccount.com"

gcloud config set project "$PROJECT"
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com storage.googleapis.com

# 1) 狀態 bucket (開版本控管:狀態檔被寫壞時可以救回舊版),並上傳目前本機的模擬帳戶狀態
gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access
gcloud storage buckets update "gs://$BUCKET" --versioning
gcloud storage cp data/bot.db "gs://$BUCKET/bot.db"

# 2) 建映像 (Cloud Build) 放到 Artifact Registry
gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION"
gcloud builds submit --tag "$IMAGE" .

# 3) 專用服務帳號:只能讀寫這個 bucket,沒有其他權限
gcloud iam service-accounts create "$SA" --display-name="bybit bot runner"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$SA_EMAIL" --role=roles/storage.objectUser

# 4) Cloud Run Job (模擬交易只用公開行情,不放任何 Bybit 金鑰)
gcloud run jobs create "$JOB" --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL" \
  --set-env-vars="STATE_BUCKET=$BUCKET" --memory=512Mi --cpu=1 --max-retries=1 --task-timeout=300

# 5) 先手動跑一次,確認台灣區域連得到 Bybit (當天本機已跑過的話會顯示 skipped,屬正常)
gcloud run jobs execute "$JOB" --region="$REGION" --wait
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=$JOB" --limit=20 --format='value(textPayload)'

# 6) 排程:每 4 小時 (分針 10) 執行一次 (UTC 00:10, 04:10, 08:10, 12:10, 16:10, 20:10)
gcloud run jobs add-iam-policy-binding "$JOB" --region="$REGION" --member="serviceAccount:$SA_EMAIL" --role=roles/run.invoker   # 只給這個 Job 的執行權
gcloud scheduler jobs create http "$JOB-trigger" --location="$REGION" --schedule="10 */4 * * *" --time-zone="Etc/UTC" \
  --uri="https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/${JOB}:run" \
  --http-method=POST --oauth-service-account-email="$SA_EMAIL"

echo "完成。雲端開始跑之後,記得用 crontab -e 移除本機的排程,避免兩邊各跑一份狀態。"
echo "看成效: gcloud storage cp gs://$BUCKET/bot.db data/bot.db && .venv/bin/python main.py report"
