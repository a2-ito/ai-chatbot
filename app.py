import os
import logging
from slack_bolt import App
import openai
import commonmarkslack

# ログ設定: LOG_LEVEL 環境変数で制御（DEBUG, INFO, WARNING, ERROR）
log_level = (os.getenv("LOG_LEVEL") or "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
logger.debug("app.py 初期化開始")

app = App(
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
    token=os.environ["SLACK_BOT_TOKEN"],
    process_before_response=True,
)

openai.api_key = os.environ["OPENAI_API_KEY"]

parser = commonmarkslack.Parser()
renderer = commonmarkslack.SlackRenderer()

def get_bot_user_id():
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

botUserId = get_bot_user_id()
logger.debug("botUserId: %s", botUserId)

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

import time
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
            "content": """あなたは Chief AI Officer です。
        あなたは社員のカウンターパートとして社員の親身になって相談に乗ってください。""",
        })

        threadContent.append({
            "role": "user",
            "content": text,
        })

        for message in threadMessages:
            if message["user"] == botUserId:
                threadContent.append({
                    "role": "assistant",
                    "content": message["text"],
                })
            else:
                threadContent.append({
                    "role": "user",
                    "content": message["text"],
                })

        logger.debug("OpenAI API 呼び出し messages=%d 件", len(threadContent))
        res = openai.ChatCompletion.create(
            model="gpt-5-mini",
            messages=threadContent,
            # tools=[
            #     {
            #         "type": "mcp",
            #         "server_label": "notion",
            #         "server_url": "https://mcp.notion.com/sse",
            #         "authorization": os.environ["NOTION_SECRET"],
            #     },
            # ]
        )
        resText = res.choices[0]["message"]["content"].strip()
        logger.info("OpenAI 応答取得成功 長さ=%d", len(resText))

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

#@app.event("app_mention")
def mention_handler(body, say):
    mention = body["event"]
    text = mention["text"]
    channel = mention["channel"]
    thread_ts = mention["ts"]

    print(f"メンションされました: {text}")

    # スレッドでテキストをオウム返し
    say(text=text, channel=channel, thread_ts=thread_ts)

@app.event("message")
def handle_message_events(body, logger):
    logger.info(body)

if __name__ == "__main__":
    app.start()

# AWS Lambda
from slack_bolt.adapter.aws_lambda import SlackRequestHandler

def handler(event, context):
    logger.debug("Lambda handler 呼び出し event_keys=%s", list(event.keys()) if isinstance(event, dict) else type(event))
    try:
        slack_handler = SlackRequestHandler(app=app)
        result = slack_handler.handle(event, context)
        logger.debug("Lambda handler 完了 status=%s", result.get("statusCode") if isinstance(result, dict) else "N/A")
        return result
    except Exception as e:
        logger.exception("Lambda handler 失敗: %s", e)
        raise
