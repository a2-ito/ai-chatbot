from __future__ import annotations

import os
import json
import logging
import re
from functools import lru_cache
from typing import Any

from slack_bolt import App
import commonmarkslack
# llama_cpp は重いネイティブlib。ack 経路のコールド初期化を軽くするため、
# モジュール読み込み時ではなく get_llm() 内で遅延 import する。

# ログ設定: LOG_LEVEL 環境変数で制御（DEBUG, INFO, WARNING, ERROR）
log_level = (os.getenv("LOG_LEVEL") or "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
logger.debug("app.py 初期化開始")

# --- LLM 設定（環境変数で上書き可） ---------------------------------------
# MODEL_PATH はイメージに同梱した GGUF ファイルを指す。
MODEL_PATH = os.environ.get("MODEL_PATH", "/opt/model/model.gguf")
# コンテキスト長。Lambda のメモリ節約のため小さめに保つ。
N_CTX = int(os.environ.get("N_CTX", "2048"))
# CPU スレッド数。Lambda は 1769MB あたり約 1 vCPU。
N_THREADS = int(os.environ.get("N_THREADS", str(os.cpu_count() or 2)))
# 生成パラメータの既定値。
DEFAULT_MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "256"))
DEFAULT_TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.7"))

SYSTEM_PROMPT = """あなたは Chief AI Officer です。
あなたは社員のカウンターパートとして社員の親身になって相談に乗ってください。"""


@lru_cache(maxsize=1)
def get_llm() -> "Llama":
    """モデルを一度だけロードし、コンテナの生存中はキャッシュして再利用する。"""
    from llama_cpp import Llama  # 遅延import（ここで初めてネイティブlibを読み込む）

    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(
            f"Model file not found at {MODEL_PATH}. "
            "ビルド前に scripts/download_model.sh を実行したか確認してください。"
        )
    logger.info("LLM ロード開始 path=%s n_ctx=%d n_threads=%d", MODEL_PATH, N_CTX, N_THREADS)
    llm = Llama(
        model_path=MODEL_PATH,
        n_ctx=N_CTX,
        n_threads=N_THREADS,
        verbose=False,
    )
    logger.info("LLM ロード完了")
    return llm


# 推論モデル(Qwen3 等)が出力する思考ブロックを除去する。
# 閉じた <think>...</think> を削除。max_tokens 切れで未クローズなら、
# <think> 以降は思考のみ＝回答なしとみなして空にする。
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_think(text: str) -> str:
    text = _THINK_RE.sub("", text)
    if "<think>" in text:  # 未クローズの思考（回答が出る前にトークン切れ）
        text = text.split("<think>", 1)[0]
    return text.strip()


def chat(messages: list[dict[str, str]], max_tokens: int, temperature: float) -> str:
    """messages 形式の会話履歴から応答テキストを生成する。"""
    llm = get_llm()
    result = llm.create_chat_completion(
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return strip_think(result["choices"][0]["message"]["content"])


app = App(
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
    token=os.environ["SLACK_BOT_TOKEN"],
    process_before_response=True,
    # 起動時の自動 auth.test を無効化（ダミートークンでのローカル検証を可能にする）。
    # bot user_id の取得と疎通確認は get_bot_user_id() で個別に行う。
    token_verification_enabled=False,
)

parser = commonmarkslack.Parser()
renderer = commonmarkslack.SlackRenderer()

@lru_cache(maxsize=1)
def get_bot_user_id():
    # auth.test は通信を伴うため、モジュール読み込み時ではなく初回利用時に遅延実行し、
    # 結果をコンテナ生存中キャッシュする（ack 経路のコールド初期化を軽くするため）。
    try:
        logger.debug("auth_test 実行中")
        result = app.client.auth_test(
            token=os.environ["SLACK_BOT_TOKEN"]
        )
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

        threadContent.append({
            "role": "system",
            "content": SYSTEM_PROMPT,
        })

        threadContent.append({
            "role": "user",
            "content": text,
        })

        for message in threadMessages:
            if message["user"] == get_bot_user_id():
                threadContent.append({
                    "role": "assistant",
                    "content": message["text"],
                })
            else:
                threadContent.append({
                    "role": "user",
                    "content": message["text"],
                })

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
    lazy=[run_long_process]
)

@app.event("message")
def handle_message_events(body, logger):
    logger.info(body)

if __name__ == "__main__":
    app.start()

# AWS Lambda
from slack_bolt.adapter.aws_lambda import SlackRequestHandler


def _is_direct_llm_request(event: Any) -> bool:
    """Slack(API Gateway)イベント以外に、直接 prompt/messages を渡す呼び出しを許可する。

    ローカル RIE での動作確認（scripts/run_local.sh）や手動 invoke 用。
    Slack 経由は body/headers/requestContext を持つため、それらが無く
    prompt または messages を持つ場合のみ直接推論とみなす。
    """
    return (
        isinstance(event, dict)
        and "requestContext" not in event
        and ("prompt" in event or "messages" in event)
    )


def _direct_llm_handler(event: dict[str, Any]) -> dict[str, Any]:
    import time

    prompt = event.get("prompt")
    messages = event.get("messages")
    max_tokens = int(event.get("max_tokens", DEFAULT_MAX_TOKENS))
    temperature = float(event.get("temperature", DEFAULT_TEMPERATURE))

    if not messages:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

    # モデルロード時間（lru_cache 済みならほぼ 0。コールド初回のみ実ロード時間）。
    t_load = time.time()
    llm = get_llm()
    model_load_sec = round(time.time() - t_load, 3)

    # 生成時間のみを計測して tokens/sec を算出（ロード・ネットワークを除外）。
    t_gen = time.time()
    result = llm.create_chat_completion(
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    gen_sec = round(time.time() - t_gen, 3)

    text = strip_think(result["choices"][0]["message"]["content"])
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
