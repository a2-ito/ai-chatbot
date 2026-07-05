# AWS Lambda Python base image (Amazon Linux 2023).
# This image already bundles the Lambda Runtime Interface Emulator (RIE),
# so the exact same image runs both locally and on AWS Lambda.
FROM public.ecr.aws/lambda/python:3.12

# --- Build dependencies for compiling llama-cpp-python ----------------------
# llama-cpp-python builds the native llama.cpp library from source, so we
# need a C/C++ toolchain and cmake. These are only used at build time.
RUN dnf install -y gcc gcc-c++ make cmake && dnf clean all

# --- Python dependencies ----------------------------------------------------
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
# Force a CPU-only build (no CUDA/Metal) optimized for the Lambda runtime.
ENV CMAKE_ARGS="-DGGML_NATIVE=OFF -DGGML_CUDA=OFF -DGGML_METAL=OFF"
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# --- Model ------------------------------------------------------------------
# The GGUF model is downloaded into ./model by scripts/download_model.sh
# BEFORE building, then baked into the image at /opt/model.
COPY model/model.gguf /opt/model/model.gguf
ENV MODEL_PATH=/opt/model/model.gguf

# --- Application ------------------------------------------------------------
COPY app.py ${LAMBDA_TASK_ROOT}/

# Lambda handler entry point: <file>.<function>
CMD ["app.handler"]
