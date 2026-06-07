#!/usr/bin/env bash
#
# Build the Lambda container image.
# Ensures the model has been downloaded first.
#
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-ai-chat}"
TAG="${TAG:-local}"
# Build platform. Default to arm64 so it builds NATIVELY on Apple Silicon
# (no QEMU emulation -> avoids the AL2023 "Failed to find vdso DT_HASH"
# crash). Deploy to an arm64/Graviton Lambda to match. Set to linux/amd64
# only if you target an x86_64 Lambda (requires Rosetta or an x86 host).
PLATFORM="${PLATFORM:-linux/arm64}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ ! -f "${ROOT_DIR}/model/model.gguf" ]]; then
  echo "Model not found. Running download_model.sh ..."
  "${SCRIPT_DIR}/download_model.sh"
fi

echo "Building ${IMAGE_NAME}:${TAG} (platform: ${PLATFORM}) ..."
docker build \
  --platform "${PLATFORM}" \
  -t "${IMAGE_NAME}:${TAG}" \
  "${ROOT_DIR}"

echo "Done. Image: ${IMAGE_NAME}:${TAG}"
