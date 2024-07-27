import os
from slack_bolt import App
# import openai
import google.generativeai as genai

app = App(
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
    token=os.environ["SLACK_BOT_TOKEN"],
    process_before_response=True,
)

# openai.api_key = os.environ["OPENAI_API_KEY"]
GOOGLE_API_KEY=os.getenv('GOOGLE_API_KEY')
genai.configure(api_key=GOOGLE_API_KEY)
gemini_pro = genai.GenerativeModel("gemini-pro")

def get_bot_user_id():
    try:
        result = app.client.auth_test(
            token=os.environ["SLACK_BOT_TOKEN"]
        )
        return result["user_id"]
    except Exception as e:
        print(e)

botUserId = get_bot_user_id()

def respond_to_slack_within_3_seconds(body, ack):
    text = body.get("text")
    if text is None or len(text) == 0:
        ack(":x: Usage: /start-process (description here)")
    else:
        ack(f"Accepted! (task: {body['text']})")

def fetch_thread_messages(channel, thread_ts):
    thread_messages_response = app.client.conversations_replies(
        channel=channel,
        ts=thread_ts,
    )
    #console.log(result.messages);
    print(thread_messages_response["messages"])
    return thread_messages_response["messages"]

import time
def run_long_process(body, say):
    mention = body["event"]
    text = mention["text"]
    channel = mention["channel"]

    threadMessages = []
    if "thread_ts" in mention:
        thread_ts = mention["thread_ts"]
        threadMessages = fetch_thread_messages(channel,thread_ts)
    else:
        thread_ts = mention["ts"]

    threadContent = []

    threadContent.append(text)

    for message in threadMessages:
        threadContent.append(message["text"])

    print(threadContent)
    res = gemini_pro.generate_content(threadContent)

    resText = res.text

    print(f"メンションされました: {resText}")
    say(text=resText, channel=channel, thread_ts=thread_ts)

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
    slack_handler = SlackRequestHandler(app=app)
    return slack_handler.handle(event, context)
