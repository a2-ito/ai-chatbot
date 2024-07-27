# ai-chat

This is a chatbot that uses OpenAI or Gemini API.
OpenAI or Gemini API を利用した AI チャットボットです。

## Description

### Prerequisites
- Python 3.9 or 3.11 (Gemini)
- serverless framework が利用できる AWS account

### 1. Slack App の作成
- Permissions の設定
```
app_mentions:read
channels:history
channels:read
chat:write
groups:history
im:history
mpim:history
iapp_mentions:read
channels:history
channels:read
```
- ワークプレイスへのインストール
- アイコンの設定（任意）

### 2. configuration
- `.env` ファイルの構成
  - `SLACK_BOT_TOKEN`
    - Bot User OAuth Token の値を設定
  - `SLACK_SIGNING_SECRET`
    - Signing Secret の値を取得h
  - `OPENAI_API_KEY`

### 3. serverless framework を利用したデプロイ
```
make
```

## tips
- scope permissions を設定しないとワークプレイスへインストールできなかった

