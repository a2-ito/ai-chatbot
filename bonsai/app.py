from __future__ import annotations

import os
import json
import time
import atexit
import logging
import subprocess
from functools import lru_cache
from typing import Any

import requests
from slack_bolt import App
import commonmarkslack

# log 設定: LOG_LEVEL 環境変数で制御（DEBUG, INFO, WARNING, ERROR）。
# Lambda ではランタイムが起動時にルートロガーへハンドラを付けているため
# logging.basicConfig() は no-op になる。ハンドラ有無で分岐する（open-llm と同様）。
log_level = (os.getenv("LOG_LEVEL") or "INFO").upper()
_level = getattr(logging, log_level, logging.INFO)
_root = logging.getLogger()
if _root.handlers:  # Lambda 実行環境（既存ハンドラあり）
    _root.setLevel(_level)
else:  # ローカル実行
    logging.basicConfig(
        level=_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
logger = logging.getLogger(__name__)
logger.setLevel(_level)
logger.debug("bonsai/app.py 初期化開始")

# --- LLM 設定（環境変数で上書き可） ---------------------------------------
# Bonsai は ternary(Q2_0) 量子化のため PrismML fork の llama.cpp が必要。
# Python バインディングが無いので、同梱した OpenAI 互換の llama-server を
# サブプロセスで起動し、localhost 経由で叩く。
MODEL_PATH = os.environ.get("MODEL_PATH", "/opt/model/model.gguf")
LLAMA_SERVER_BIN = os.environ.get("LLAMA_SERVER_BIN", "/opt/llama/bin/llama-server")
LLAMA_HOST = os.environ.get("LLAMA_HOST", "127.0.0.1")
LLAMA_PORT = int(os.environ.get("LLAMA_PORT", "8080"))
# コンテキスト長。メモリ節約のため小さめに保つ。
N_CTX = int(os.environ.get("N_CTX", "2048"))
# CPU スレッド数。Lambda は 1769MB あたり約 1 vCPU。
N_THREADS = int(os.environ.get("N_THREADS", str(os.cpu_count() or 2)))
# サーバ起動（モデルロード）を待つ最大秒数。コールドでのロードに余裕を持たせる。
SERVER_START_TIMEOUT = int(os.environ.get("SERVER_START_TIMEOUT", "300"))
# 生成パラメータの既定値。
DEFAULT_MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "256"))
DEFAULT_TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.7"))

_BASE_URL = f"http://{LLAMA_HOST}:{LLAMA_PORT}"

SYSTEM_PROMPT = """あなたは Chief AI Officer です。
あなたは社員のカウンターパートとして社員の親身になって相談に乗ってください。"""


@lru_cache(maxsize=1)
def ensure_server() -> str:
    """llama-server を一度だけ起動し、/health が通るまで待って base URL を返す。

    Lambda コンテナはリクエストをまたいでプロセスが生存するため、
    lru_cache により初回のみ起動し、以降はウォームで再利用される。
    """
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(
            f"Model file not found at {MODEL_PATH}. "
            "ビルド前に scripts/download_model.sh を実行したか確認してください。"
        )
    if not os.path.exists(LLAMA_SERVER_BIN):
        raise FileNotFoundError(
            f"llama-server not found at {LLAMA_SERVER_BIN}. "
            "Dockerfile の fork ビルドが成功しているか確認してください。"
        )

    cmd = [
        LLAMA_SERVER_BIN,
        "-m", MODEL_PATH,
        "-c", str(N_CTX),
        "-t", str(N_THREADS),
        "--host", LLAMA_HOST,
        "--port", str(LLAMA_PORT),
        # CPU 実行（GPU レイヤ 0）。fork README 推奨の flash-attention を有効化。
        "-ngl", "0",
        "-fa", "1",
    ]
    logger.info("llama-server 起動: %s", " ".join(cmd))
    # 書き込み可能な /tmp を作業ディレクトリにする（Lambda は /tmp のみ書込可）。
    proc = subprocess.Popen(cmd, cwd="/tmp")

    # プロセスをコンテナ終了時に確実に片付ける。
    atexit.register(lambda: proc.poll() is None and proc.terminate())

    deadline = time.time() + SERVER_START_TIMEOUT
    health_url = f"{_BASE_URL}/health"
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"llama-server が起動前に終了しました (exit={proc.returncode})"
            )
        try:
            r = requests.get(health_url, timeout=2)
            if r.status_code == 200:
                logger.info("llama-server 起動完了 (%s)", health_url)
                return _BASE_URL
        except requests.RequestException:
            pass  # 起動待ち
        time.sleep(1)

    proc.terminate()
    raise TimeoutError(
        f"llama-server が {SERVER_START_TIMEOUT}s 以内に起動しませんでした"
    )


def chat_completion(
    messages: list[dict[str, str]], max_tokens: int, temperature: float
) -> dict[str, Any]:
    """OpenAI 互換 /v1/chat/completions を叩き、生レスポンス(JSON)を返す。"""
    base = ensure_server()
    payload = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    # 生成は長くなりうるため read タイムアウトは大きめに。
    r = requests.post(
        f"{base}/v1/chat/completions",
        json=payload,
        timeout=(5, SERVER_START_TIMEOUT),
    )
    r.raise_for_status()
    return r.json()


def chat(messages: list[dict[str, str]], max_tokens: int, temperature: float) -> str:
    """messages 形式の会話履歴から応答テキストを生成する。"""
    result = chat_completion(messages, max_tokens, temperature)
    return result["choices"][0]["message"]["content"].strip()


app = App(
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
    token=os.environ["SLACK_BOT_TOKEN"],
    process_before_response=True,
    # ダミートークンでのローカル検証を可能にする（open-llm と同様）。
    token_verification_enabled=False,
)

parser = commonmarkslack.Parser()
renderer = commonmarkslack.SlackRenderer()


@lru_cache(maxsize=1)
def get_bot_user_id():
    try:
        logger.debug("auth_test 実行中")
        result = app.client.auth_test(token=os.environ["SLACK_BOT_TOKEN"])
        user_id = result["user_id"]
        logger.info("Bot user_id 取得成功: %s", user_id)
        return user_id
    except Exception as e:
        logger.exception("auth_test 失敗（botUserId は None になります）: %s", e)
        return None


def respond_to_slack_within_3_seconds(body, ack):
    logger.debug("respond_to_slack_within_3_seconds 呼び出し body=%s", body)
    text = body.get("text")
    if text is None or len(text) == 0:
        logger.debug("text が空のためエラーメッセージを返却")
        ack(":x: Usage: /start-process (description here)")
    else:
        logger.debug("ack 送信: task=%s", body["text"])
        ack(f"Accepted! (task: {body['text']})")


def fetch_thread_messages(channel, thread_ts):
    logger.debug("スレッドメッセージ取得開始 channel=%s thread_ts=%s", channel, thread_ts)
    try:
        thread_messages_response = app.client.conversations_replies(
            channel=channel,
            ts=thread_ts,
        )
        messages = thread_messages_response["messages"]
        logger.debug("スレッドメッセージ取得完了: %d 件", len(messages))
        return messages
    except Exception as e:
        logger.exception("conversations_replies 失敗 channel=%s thread_ts=%s: %s", channel, thread_ts, e)
        raise


def run_long_process(body, say):
    logger.info("run_long_process 開始")
    try:
        mention = body["event"]
        text = mention["text"]
        channel = mention["channel"]
        logger.debug("メンション受信 channel=%s text=%s", channel, text[:50] + "..." if len(text) > 50 else text)

        threadMessages = []
        if "thread_ts" in mention:
            thread_ts = mention["thread_ts"]
            logger.debug("スレッド内メンション thread_ts=%s", thread_ts)
            threadMessages = fetch_thread_messages(channel, thread_ts)
        else:
            thread_ts = mention["ts"]
            logger.debug("新規スレッド ts=%s", thread_ts)

        threadContent = []
        threadContent.append({"role": "system", "content": SYSTEM_PROMPT})
        threadContent.append({"role": "user", "content": text})

        for message in threadMessages:
            if message["user"] == get_bot_user_id():
                threadContent.append({"role": "assistant", "content": message["text"]})
            else:
                threadContent.append({"role": "user", "content": message["text"]})

        logger.debug("LLM 推論開始 messages=%d 件", len(threadContent))
        resText = chat(threadContent, DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE)
        logger.info("LLM 応答取得成功 長さ=%d", len(resText))

        slack_md = renderer.render(parser.parse(resText))
        say(text=slack_md, channel=channel, thread_ts=thread_ts)
        logger.info("run_long_process 完了 Slack に応答送信")
    except Exception as e:
        logger.exception("run_long_process 失敗: %s", e)
        raise


app.event("app_mention")(
    ack=respond_to_slack_within_3_seconds,
    lazy=[run_long_process],
)


@app.event("message")
def handle_message_events(body, logger):
    logger.info(body)


if __name__ == "__main__":
    app.start()

# AWS Lambda
from slack_bolt.adapter.aws_lambda import SlackRequestHandler


def _is_direct_llm_request(event: Any) -> bool:
    """Slack(API Gateway)以外に、直接 prompt/messages を渡す呼び出しを許可する。

    ローカル RIE での動作確認（scripts/run_local.sh）や手動 invoke / benchmark 用。
    """
    return (
        isinstance(event, dict)
        and "requestContext" not in event
        and ("prompt" in event or "messages" in event)
    )


def _direct_llm_handler(event: dict[str, Any]) -> dict[str, Any]:
    prompt = event.get("prompt")
    messages = event.get("messages")
    max_tokens = int(event.get("max_tokens", DEFAULT_MAX_TOKENS))
    temperature = float(event.get("temperature", DEFAULT_TEMPERATURE))

    if not messages:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

    # サーバ起動時間（ウォームならほぼ 0。コールド初回のみ実ロード時間）。
    t_load = time.time()
    ensure_server()
    model_load_sec = round(time.time() - t_load, 3)

    # 生成時間のみ計測して tokens/sec を算出。
    t_gen = time.time()
    result = chat_completion(messages, max_tokens, temperature)
    gen_sec = round(time.time() - t_gen, 3)

    text = result["choices"][0]["message"]["content"].strip()
    usage = result.get("usage", {}) or {}
    completion_tokens = usage.get("completion_tokens")
    tokens_per_sec = (
        round(completion_tokens / gen_sec, 2)
        if completion_tokens and gen_sec > 0
        else None
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "output": text,
                "model": os.path.basename(MODEL_PATH),
                "usage": usage,
                "gen_sec": gen_sec,
                "model_load_sec": model_load_sec,
                "tokens_per_sec": tokens_per_sec,
            },
            ensure_ascii=False,
        ),
    }


def handler(event, context):
    logger.debug("Lambda handler 呼び出し event_keys=%s", list(event.keys()) if isinstance(event, dict) else type(event))
    # ローカル検証・手動 invoke 用の直接推論パス
    if _is_direct_llm_request(event):
        logger.debug("直接 LLM リクエストとして処理")
        return _direct_llm_handler(event)
    try:
        slack_handler = SlackRequestHandler(app=app)
        result = slack_handler.handle(event, context)
        logger.debug("Lambda handler 完了 status=%s", result.get("statusCode") if isinstance(result, dict) else "N/A")
        return result
    except Exception as e:
        logger.exception("Lambda handler 失敗: %s", e)
        raise
