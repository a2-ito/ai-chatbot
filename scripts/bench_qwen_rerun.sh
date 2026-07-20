#!/usr/bin/env bash
#
# Qwen3-0.6B / Qwen3-0.6B-nothink / Qwen3-1.7B の3モデルだけを再計測する専用スクリプト。
#
# deploy_cold_s（新規デプロイ直後の初回invokeの真のコールド）を毎回きちんと計測するため、
# モデルごとに必ず remove（Lambda/スタック削除）→ deploy（再作成）してから benchmark を回す。
#   switch(DL) → build → remove → deploy → benchmark
#
# think/nothink は SYSTEM_PROMPT 末尾の /no_think 有無で切替える（app.py の NO_THINK env）。
# NO_THINK はシェル環境経由で serverless の ${env:NO_THINK} に伝播する。
#
# 結果は benchmark_qwen_rerun.csv に追記する（既定の benchmark.csv とは分離）。
# bench_all.sh と違い再開スキップはしない（毎回まっさらに計測し直す用途）。
#
# 使い方:
#   bash scripts/bench_qwen_rerun.sh                 # 3モデル全部
#   bash scripts/bench_qwen_rerun.sh Qwen3-1.7B      # 指定ラベルのみ（複数指定可）
#
# 環境変数: AWS_PROFILE(既定 playground) / AWS_REGION(既定 us-east-1) / BENCH_CSV / BENCH_MAX_TOKENS
#
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export AWS_PROFILE="${AWS_PROFILE:-playground}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export BENCH_CSV="${BENCH_CSV:-benchmark_qwen_rerun.csv}"
# think モードは <think> がトークンを消費するため、256 だと思考だけで打ち切られて回答が空になりやすい。
export BENCH_MAX_TOKENS="${BENCH_MAX_TOKENS:-1024}"
# deploy_cold_s（デプロイ直後の初回invoke）と cold_start_s（リサイクルコールド）の両方を計測する。
# 各モデルで remove→deploy 済みなので DEPLOY_COLD=1 が正確に効く（いずれも benchmark.py の既定）。
export DEPLOY_COLD="1"
export RECYCLE_COLD="1"

# "表示ラベル|HF_REPO|HF_FILE|NO_THINK"
ALL_MODELS=(
  "Qwen3-0.6B|bartowski/Qwen_Qwen3-0.6B-GGUF|Qwen_Qwen3-0.6B-Q4_K_M.gguf|0"
  "Qwen3-0.6B-nothink|bartowski/Qwen_Qwen3-0.6B-GGUF|Qwen_Qwen3-0.6B-Q4_K_M.gguf|1"
  "Qwen3-1.7B|bartowski/Qwen_Qwen3-1.7B-GGUF|Qwen_Qwen3-1.7B-Q4_K_M.gguf|0"
)

log() { printf '\n\033[1;36m[bench-qwen] %s\033[0m\n' "$*"; }

creds_ok() { aws sts get-caller-identity >/dev/null 2>&1; }

# 引数でラベルを指定するとそのモデルだけ計測する（指定なしなら全モデル）。
MODELS=()
if [ "$#" -gt 0 ]; then
  for want in "$@"; do
    found=0
    for entry in "${ALL_MODELS[@]}"; do
      if [ "${entry%%|*}" = "$want" ]; then
        MODELS+=("$entry")
        found=1
        break
      fi
    done
    if [ "$found" -eq 0 ]; then
      log "不明なラベル: $want （有効: $(printf '%s ' "${ALL_MODELS[@]%%|*}"))"
      exit 2
    fi
  done
else
  MODELS=("${ALL_MODELS[@]}")
fi
log "計測対象: $(printf '%s ' "${MODELS[@]%%|*}")"

total=${#MODELS[@]}
idx=0
for entry in "${MODELS[@]}"; do
  idx=$((idx + 1))
  IFS='|' read -r LABEL HF_REPO HF_FILE NO_THINK <<<"$entry"
  export NO_THINK

  if ! creds_ok; then
    log "AWS 認証が無効です。次を実行して再ログイン後、このスクリプトを再実行してください:"
    echo "    aws sso login --profile $AWS_PROFILE"
    exit 2
  fi

  log "[$idx/$total] $LABEL (NO_THINK=$NO_THINK) : switch(download)"
  if ! make switch HF_REPO="$HF_REPO" HF_FILE="$HF_FILE"; then
    log "$LABEL: download 失敗 → 次のモデルへ"
    continue
  fi

  log "[$idx/$total] $LABEL : build"
  if ! make build; then
    log "$LABEL: build 失敗 → 次のモデルへ"
    continue
  fi

  # deploy_cold_s を毎回計測するため、既存スタックを必ず削除してから再デプロイする。
  log "[$idx/$total] $LABEL : remove (既存 Lambda/スタックを破棄・無ければ無視)"
  make remove || true

  log "[$idx/$total] $LABEL : deploy (NO_THINK=$NO_THINK)"
  if ! make deploy; then
    log "deploy 失敗（SSO 期限切れの可能性大）。再ログイン後に再実行してください:"
    echo "    aws sso login --profile $AWS_PROFILE"
    exit 2
  fi

  log "[$idx/$total] $LABEL : benchmark (deploy_cold_s 計測)"
  MODEL_LABEL="$LABEL" make benchmark || log "$LABEL: benchmark 失敗（記録のみ）"
done

log "3モデル完了。結果は $BENCH_CSV に追記済み。"
