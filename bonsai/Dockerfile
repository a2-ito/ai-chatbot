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
# LLAMA_BUILD_EXAMPLES=OFF: llama-server だけをビルドする。
# cmake --install は使わない: 全ターゲット(tools/batched-bench 等)のインストール
#   ルールが生成されるため、--target llama-server だけをビルドすると未ビルドの
#   バイナリ/共有ライブラリを探して失敗する。必要な成果物は次の RUN で直接拾う。
RUN cmake -B build \
      -DCMAKE_BUILD_TYPE=Release \
      -DGGML_NATIVE=OFF \
      -DLLAMA_CURL=OFF \
      -DLLAMA_BUILD_TESTS=OFF \
      -DLLAMA_BUILD_EXAMPLES=OFF \
      -DLLAMA_BUILD_SERVER=ON \
 && cmake --build build --target llama-server -j "$(nproc)"

# 実行に必要な成果物（llama-server バイナリと全共有ライブラリ）を集約する。
# バイナリが無ければ即失敗させる（壊れたイメージを作らない）。.so は
# バックエンドを含め build 配下から漏れなく拾う。cp -P で SONAME の
# シンボリックリンク(libggml-base.so.0 → *.so.0.x.y)も保持する。実体だけ
# コピーするとローダーが .so.0 を見つけられず exit=127 で落ちる。
RUN mkdir -p /opt/llama/bin /opt/llama/lib \
 && cp -f build/bin/llama-server /opt/llama/bin/ \
 && find build -name '*.so*' -exec cp -Pf {} /opt/llama/lib/ \;

# 依存する共有ライブラリが実行環境の探索パス(/opt/llama/lib)で解決できるか
# ビルド時に検証する。"not found" が残っていれば即失敗させ、壊れたイメージが
# ECR に上がるのを防ぐ。
RUN LD_LIBRARY_PATH=/opt/llama/lib ldd /opt/llama/bin/llama-server | tee /tmp/ldd.txt \
 && if grep -q 'not found' /tmp/ldd.txt; then \
      echo 'ERROR: llama-server に未解決の共有ライブラリがあります'; exit 1; \
    fi

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
