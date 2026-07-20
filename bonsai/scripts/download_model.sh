#!/usr/bin/env bash
#
# Bonsai(PrismML) の GGUF(Q2_0) を Hugging Face から ./model/model.gguf に
# ダウンロードし、コンテナイメージに焼き込めるようにする。
#
# モデルは ../models.env で選択する（使うものだけコメントイン）。
# HF_REPO / HF_FILE の優先順位:
#   1. 既に設定済みの環境変数（例: make switch HF_REPO=...）
#   2. ../models.env
#   3. 下記の組み込みデフォルト（Ternary-Bonsai-1.7B Q2_0）
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MODEL_DIR="${ROOT_DIR}/model"

# 明示指定された環境変数は models.env より優先する。
_ENV_HF_REPO="${HF_REPO:-}"
_ENV_HF_FILE="${HF_FILE:-}"

if [[ -f "${ROOT_DIR}/models.env" ]]; then
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/models.env"
fi

# 優先順位: 明示 env > models.env > 組み込みデフォルト。
HF_REPO="${_ENV_HF_REPO:-${HF_REPO:-prism-ml/Ternary-Bonsai-1.7B-gguf}}"
HF_FILE="${_ENV_HF_FILE:-${HF_FILE:-Ternary-Bonsai-1.7B-Q2_0.gguf}}"
OUT="${MODEL_DIR}/model.gguf"

URL="https://huggingface.co/${HF_REPO}/resolve/main/${HF_FILE}?download=true"

mkdir -p "${MODEL_DIR}"

if [[ -f "${OUT}" ]]; then
  echo "Model already exists at ${OUT} (delete it to re-download)."
  exit 0
fi

echo "Downloading ${HF_REPO}/${HF_FILE} ..."
# -L follows redirects (HF serves files via a CDN redirect).
curl -L --fail --progress-bar -o "${OUT}.tmp" "${URL}"
mv "${OUT}.tmp" "${OUT}"

echo "Saved to ${OUT}"
ls -lh "${OUT}"
