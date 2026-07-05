#!/usr/bin/env bash
#
# Run the Lambda container locally using the built-in Runtime Interface
# Emulator (RIE), then send a test invocation.
#
# Usage:
#   ./scripts/run_local.sh                 # start the container (foreground)
#   ./scripts/run_local.sh "Your prompt"   # start, invoke once, then keep running
#
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-ai-chat}"
TAG="${TAG:-local}"
CONTAINER_NAME="${CONTAINER_NAME:-ai-chat-local}"
PORT="${PORT:-9000}"
# Must match the platform the image was built for (see build.sh).
PLATFORM="${PLATFORM:-linux/arm64}"
# Match Lambda's memory/CPU as closely as you like; here we just cap threads.
MEMORY="${MEMORY:-3g}"

# app.py は読み込み時に Slack 認証情報を要求する。ローカルの LLM 単体検証では
# 実値が不要なためダミーを渡す（auth_test は失敗するが処理は継続する）。
SLACK_SIGNING_SECRET="${SLACK_SIGNING_SECRET:-dummy}"
SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN:-dummy}"

# Clean up any previous container.
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

# Wait for the RIE to be ready.
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
echo ""
echo "Invoke again manually, e.g.:"
echo "  curl -s http://localhost:${PORT}/2015-03-31/functions/function/invocations \\"
echo "    -d '{\"prompt\": \"Hello!\", \"max_tokens\": 128}' | python3 -m json.tool"
