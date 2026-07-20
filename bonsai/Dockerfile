# =============================================================================
# Bonsai (PrismML) 用 Lambda コンテナイメージ
# =============================================================================
# Bonsai の ternary(Q2_0) 量子化は mainline llama.cpp では動かず、PrismML の
# fork（PrismML-Eng/llama.cpp の prism ブランチ）が Q2_0(CPU: NEON/generic) を
# 追加している。Python バインディング(llama-cpp-python)は非対応のため、
# fork をソースビルドして OpenAI 互換の `llama-server` を同梱し、app.py から
# localhost に対して /v1/chat/completions で叩く構成にする。
#
# multi-stage: builder で fork をビルドし、最終イメージには llama-server と
# 共有ライブラリ・モデルだけを載せてサイズとコールドを抑える。

# --- builder: fork llama.cpp を CPU 向けにソースビルド -----------------------
FROM public.ecr.aws/lambda/python:3.12 AS builder

# ビルドツール一式（最終イメージには持ち込まない）。
RUN dnf install -y gcc gcc-c++ make cmake git && dnf clean all

# PrismML fork の prism ブランチ（Q2_0 対応。デフォルトブランチ）。
# コミット固定したい場合は ARG BONSAI_LLAMACPP_REF を上書きする。
ARG BONSAI_LLAMACPP_REPO=https://github.com/PrismML-Eng/llama.cpp
ARG BONSAI_LLAMACPP_REF=prism
RUN git clone --depth 1 -b "${BONSAI_LLAMACPP_REF}" "${BONSAI_LLAMACPP_REPO}" /src/llama.cpp

WORKDIR /src/llama.cpp
# GGML_NATIVE=OFF: ビルドホスト固有の CPU 命令に最適化させない（実行環境での
#   "illegal instruction" を回避。arm64 の NEON は既定で有効）。
# LLAMA_CURL=OFF: モデルは同梱するため HTTP ダウンロード機能は不要（libcurl 依存を外す）。
RUN cmake -B build \
      -DCMAKE_BUILD_TYPE=Release \
      -DGGML_NATIVE=OFF \
      -DLLAMA_CURL=OFF \
      -DLLAMA_BUILD_TESTS=OFF \
      -DLLAMA_BUILD_EXAMPLES=ON \
      -DLLAMA_BUILD_SERVER=ON \
 && cmake --build build --target llama-server -j "$(nproc)" \
 && cmake --install build --prefix /opt/llama

# 共有ライブラリを含め、実行に必要な成果物を確実に集約する。
# （cmake --install が bin/llama-server と lib/*.so を配置するが、
#   一部バックエンド .so は build/bin にしか無いことがあるため両方拾う。）
RUN mkdir -p /opt/llama/bin /opt/llama/lib \
 && cp -f build/bin/llama-server /opt/llama/bin/ 2>/dev/null || true \
 && find build -name '*.so' -exec cp -f {} /opt/llama/lib/ \; 2>/dev/null || true

# --- final: 実行イメージ -----------------------------------------------------
FROM public.ecr.aws/lambda/python:3.12

# llama.cpp の CPU ビルドは OpenMP(libgomp) にリンクする。ランタイムを入れる。
RUN dnf install -y libgomp && dnf clean all

# fork のビルド成果物（llama-server + 共有ライブラリ）を取り込む。
COPY --from=builder /opt/llama /opt/llama
ENV LLAMA_SERVER_BIN=/opt/llama/bin/llama-server \
    LD_LIBRARY_PATH=/opt/llama/lib

# --- Python 依存 -------------------------------------------------------------
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# --- モデル ------------------------------------------------------------------
# Q2_0 GGUF を scripts/download_model.sh で ./model に取得してからビルドし、
# イメージに焼き込む。
COPY model/model.gguf /opt/model/model.gguf
ENV MODEL_PATH=/opt/model/model.gguf

# --- アプリケーション --------------------------------------------------------
COPY app.py ${LAMBDA_TASK_ROOT}/

# Lambda ハンドラ: <file>.<function>
CMD ["app.handler"]
