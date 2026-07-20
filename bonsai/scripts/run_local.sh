#!/usr/bin/env bash
#
# Bonsai 用 Lambda コンテナを RIE でローカル起動し、テスト invoke を送る。
# 初回 invoke で内部の llama-server が起動＋モデルロードするため、応答まで
# 時間がかかる（コールド相当）。
#
# Usage:
#   ./scripts/run_local.sh                 # 起動
#   ./scripts/run_local.sh "Your prompt"   # 起動して1回 invoke
#
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-ai-chat-bonsai}"
TAG="${TAG:-local}"
CONTAINER_NAME="${CONTAINER_NAME:-ai-chat-bonsai-local}"
PORT="${PORT:-9000}"
PLATFORM="${PLATFORM:-linux/arm64}"
# llama-server 用に余裕を持たせる。
MEMORY="${MEMORY:-4g}"

# app.py は読み込み時に Slack 認証情報を要求する。ローカルの LLM 単体検証では
# 実値が不要なためダミーを渡す（token_verification_enabled=False で継続する）。
SLACK_SIGNING_SECRET="${SLACK_SIGNING_SECRET:-dummy}"
SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN:-dummy}"

docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

echo "Starting ${IMAGE_NAME}:${TAG} on http://localhost:${PORT} ..."
docker run -d \
  --name "${CONTAINER_NAME}" \
  --platform "${PLATFORM}" \
  -p "${PORT}:8080" \
  --memory "${MEMORY}" \
  -e SLACK_SIGNING_SECRET="${SLACK_SIGNING_SECRET}" \
  -e SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN}" \
  "${IMAGE_NAME}:${TAG}" >/dev/null

echo "Waiting for the emulator to come up ..."
for _ in $(seq 1 30); do
  if curl -s -o /dev/null "http://localhost:${PORT}/2015-03-31/functions/function/invocations" -d '{}'; then
    break
  fi
  sleep 1
done

PROMPT="${1:-日本語で自己紹介してください。}"

echo ""
echo "Invoking with prompt: ${PROMPT}"
echo "（初回は llama-server 起動＋モデルロードのため時間がかかります）"
echo "----------------------------------------"
curl -s "http://localhost:${PORT}/2015-03-31/functions/function/invocations" \
  -d "$(printf '{"prompt": %s, "max_tokens": 256}' "$(printf '%s' "${PROMPT}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")" \
  | python3 -m json.tool --no-ensure-ascii 2>/dev/null \
  || echo "(invocation failed — check 'docker logs ${CONTAINER_NAME}')"

echo ""
echo "----------------------------------------"
echo "Container '${CONTAINER_NAME}' is still running."
echo "  Logs:  docker logs -f ${CONTAINER_NAME}"
echo "  Stop:  docker rm -f ${CONTAINER_NAME}"
