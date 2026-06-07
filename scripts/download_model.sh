#!/usr/bin/env bash
#
# Download a small GGUF model from Hugging Face into ./model/model.gguf
# so it can be baked into the container image.
#
# The model is selected in ../models.env (comment in the one you want).
# Precedence for HF_REPO / HF_FILE:
#   1. environment variables already set (e.g. `make switch HF_REPO=...`)
#   2. ../models.env
#   3. the built-in default below (Qwen2.5-0.5B-Instruct)
#
#   HF_REPO  : Hugging Face repository id
#   HF_FILE  : GGUF filename within the repo
#
set -euo pipefail

# Resolve repo root regardless of where the script is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MODEL_DIR="${ROOT_DIR}/model"

# Remember any explicitly-provided env vars; they win over models.env.
_ENV_HF_REPO="${HF_REPO:-}"
_ENV_HF_FILE="${HF_FILE:-}"

# Load the model selection file if present.
if [[ -f "${ROOT_DIR}/models.env" ]]; then
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/models.env"
fi

# Apply precedence: explicit env > models.env > built-in default.
HF_REPO="${_ENV_HF_REPO:-${HF_REPO:-Qwen/Qwen2.5-0.5B-Instruct-GGUF}}"
HF_FILE="${_ENV_HF_FILE:-${HF_FILE:-qwen2.5-0.5b-instruct-q4_k_m.gguf}}"
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
