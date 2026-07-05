#!/usr/bin/env bash
#
# models.env の複数モデルを順に
#   switch(DL) → build → remove → deploy → benchmark
# で回し、結果を benchmark.csv に追記する。
#
# 再開可能: benchmark.csv に既にあるモデル(LABEL)はスキップする。
# SSO 期限切れ等で deploy が失敗したら停止する → `aws sso login` 後に再実行すれば
# 既処理分をスキップして続きから再開する。
#
# 環境変数: AWS_PROFILE(既定 playground) / AWS_REGION(既定 us-east-1) / BENCH_CSV
#
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export AWS_PROFILE="${AWS_PROFILE:-playground}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
CSV="${BENCH_CSV:-benchmark.csv}"

# "表示ラベル|HF_REPO|HF_FILE"
MODELS=(
  "Llama-3.2-1B|bartowski/Llama-3.2-1B-Instruct-GGUF|Llama-3.2-1B-Instruct-Q4_K_M.gguf"
  "Llama-3.2-3B|bartowski/Llama-3.2-3B-Instruct-GGUF|Llama-3.2-3B-Instruct-Q4_K_M.gguf"
  "SmolLM2-135M|bartowski/SmolLM2-135M-Instruct-GGUF|SmolLM2-135M-Instruct-Q4_K_M.gguf"
  "SmolLM2-360M|bartowski/SmolLM2-360M-Instruct-GGUF|SmolLM2-360M-Instruct-Q4_K_M.gguf"
  "SmolLM2-1.7B|HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF|smollm2-1.7b-instruct-q4_k_m.gguf"
  # Qwen3 / Gemma3（0.3.28 でロード可能になった新アーキ）
  "Qwen3-1.7B|bartowski/Qwen_Qwen3-1.7B-GGUF|Qwen_Qwen3-1.7B-Q4_K_M.gguf"
  "Qwen3-4B|Qwen/Qwen3-4B-GGUF|Qwen3-4B-Q4_K_M.gguf"
  "Gemma3-1B|ggml-org/gemma-3-1b-it-GGUF|gemma-3-1b-it-Q4_K_M.gguf"
  "Gemma3-4B|ggml-org/gemma-3-4b-it-GGUF|gemma-3-4b-it-Q4_K_M.gguf"
  # Phi-4-mini(~4B) は重すぎて実用外（300sでtimeout実証済）のため既定除外。必要なら手動追加。
  # "Phi-4-mini|unsloth/Phi-4-mini-instruct-GGUF|Phi-4-mini-instruct-Q4_K_M.gguf"
)

log() { printf '\n\033[1;36m[bench-all] %s\033[0m\n' "$*"; }

creds_ok() { aws sts get-caller-identity >/dev/null 2>&1; }

already_done() {
  # model は CSV の2列目（timestamp/model はカンマを含まない）
  [ -f "$CSV" ] && cut -d, -f2 "$CSV" 2>/dev/null | grep -Fxq "$1"
}

total=${#MODELS[@]}
idx=0
for entry in "${MODELS[@]}"; do
  idx=$((idx + 1))
  IFS='|' read -r LABEL HF_REPO HF_FILE <<<"$entry"

  if already_done "$LABEL"; then
    log "[$idx/$total] SKIP $LABEL (benchmark.csv に既存)"
    continue
  fi

  if ! creds_ok; then
    log "AWS 認証が無効です。次を実行して再ログイン後、このスクリプトを再実行してください（既処理分はスキップ）:"
    echo "    aws sso login --profile $AWS_PROFILE"
    exit 2
  fi

  log "[$idx/$total] $LABEL : switch(download)"
  if ! make switch HF_REPO="$HF_REPO" HF_FILE="$HF_FILE"; then
    log "$LABEL: download 失敗 → 次のモデルへ"
    continue
  fi

  log "[$idx/$total] $LABEL : build"
  if ! make build; then
    log "$LABEL: build 失敗 → 次のモデルへ"
    continue
  fi

  log "[$idx/$total] $LABEL : remove (既存スタック破棄・無ければ無視)"
  make remove || true

  log "[$idx/$total] $LABEL : deploy"
  if ! make deploy; then
    log "deploy 失敗（SSO 期限切れの可能性大）。再ログイン後に再実行してください:"
    echo "    aws sso login --profile $AWS_PROFILE"
    exit 2
  fi

  log "[$idx/$total] $LABEL : benchmark"
  # 非対応モデルでもベンチは走り、CSV に失敗行を残して次へ進む。
  MODEL_LABEL="$LABEL" make benchmark || log "$LABEL: benchmark 失敗（記録のみ）"
done

log "全モデル完了。結果は $CSV に追記済み。"
