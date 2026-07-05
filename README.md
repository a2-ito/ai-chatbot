# ai-chat

Slack 上で動く AI チャットボットっしゅ。
ルート構成は **オープンLLM（量子化GGUF）を llama.cpp でコンテナ内推論**し、
**Serverless Framework のコンテナイメージ方式**で AWS Lambda にデプロイするっしゅ。
（OpenAI 版から置き換え。Gemini API 版は `gemini/` に残してあるっしゅ）

## 構成

| ファイル | 役割 |
|---|---|
| `Dockerfile` | AWS Lambda Python ベースイメージ（RIE組込）に llama-cpp-python とモデルを同梱 |
| `app.py` | Slack Bolt ハンドラ。スレッド履歴を messages 化して GGUF モデルで推論 |
| `serverless.yml` | コンテナモード設定（`provider.ecr.images`／`image:`／arm64） |
| `requirements.txt` | `slack_bolt` / `commonmark-slack` / `llama-cpp-python` |
| `models.env` | 使用モデルの選択ファイル（使うモデルだけコメントイン） |
| `scripts/download_model.sh` | HuggingFace から `models.env` で選んだ GGUF を取得 |
| `scripts/build.sh` | ローカル用 `docker build`（モデル未取得なら自動DL） |
| `scripts/run_local.sh` | RIE でローカル起動 → テスト推論 |

## 前提

- Docker（Apple Silicon でもOK。arm64 ネイティブビルド）
- Node.js / `npx`（Serverless Framework v3）
- `curl`, `python3`
- AWS アカウント（ECR / Lambda の権限）

## 1. Slack App の作成

- Permissions（OAuth scopes）の設定
```
app_mentions:read
channels:history
channels:read
chat:write
groups:history
im:history
mpim:history
```
- ワークプレイスへのインストール
- アイコンの設定（任意）

## 2. configuration（`.env`）

```
SLACK_BOT_TOKEN=...       # Bot User OAuth Token
SLACK_SIGNING_SECRET=...  # Signing Secret
NOTION_SECRET=...         # 任意
AWS_PROFILE=...           # make deploy で使用
LOG_LEVEL=INFO            # DEBUG/INFO/WARNING/ERROR
```

## 3. モデルの選択 & 取得

`models.env` を開いて、使いたいモデルの行（`HF_REPO` と `HF_FILE`）だけ
コメントインするっしゅ（有効にできるのは1モデル）。

```bash
make download        # models.env の選択を model/model.gguf に取得
make switch          # 選択を切り替える（既存削除→再取得）
make which-model     # 取得済みモデルを確認
```

選べるモデル（すべて Q4_K_M 量子化）:

| ファミリー | サイズ | 目安DL |
|---|---|---|
| Llama 3.2 | 1B / 3B | 0.8 / 2.0 GB |
| Qwen3 | 0.6B / 1.7B / 4B | 0.5 / 1.1 / 2.5 GB |
| Gemma 3 | 1B / 4B | 0.8 / 2.5 GB |
| Phi-4-mini | 4B | 2.5 GB |
| SmolLM2 | 135M / 360M / 1.7B | 0.1 / 0.3 / 1.1 GB |

## 4. ローカルで動作確認（RIE）

本番 Lambda と**同じイメージ**を RIE でローカル起動し、Slack なしで推論だけ叩けるっしゅ。

```bash
make build                                  # モデルDL → docker build
make run PROMPT="東京の観光名所を3つ教えて"   # 起動 + テスト推論
make logs                                   # ログ追跡
make stop                                   # 停止・削除
```

直接叩く場合（`app.handler` は prompt/messages 直接呼び出しにも対応）:

```bash
curl -s http://localhost:9000/2015-03-31/functions/function/invocations \
  -d '{"prompt": "自己紹介して", "max_tokens": 128}' | python3 -m json.tool --no-ensure-ascii
```

## 5. デプロイ（Serverless Framework / コンテナモード）

`sls deploy` 時に Serverless Framework が `docker build` → ECR push →
`package-type: Image` の Lambda 作成/更新まで実施するっしゅ。
**事前にモデルを取得しておく**こと（`make deploy` は `download` に依存）。

```bash
aws sso login            # もしくは aws configure / 環境変数
make deploy              # モデルDL → sls deploy（ECR push → Lambda）
make remove              # スタック削除
```

Slack の Event Subscriptions の Request URL には、デプロイ後に出力される
API Gateway の `…/slack/events` を設定するっしゅ。

## 設計上の勘所

- **zip は 250MB 上限**でモデルが入らないため、コンテナイメージ方式が必須っしゅ
  （コンテナ Lambda のイメージ上限は 10GB。量子化済み小型モデルなら余裕）
- **メモリ**: `serverless.yml` で 10240MB（最大）。Lambda はメモリに比例して CPU も増える＝推論が速い（3B クラスを動かすため大きめ）
- **タイムアウト**: コールドスタートでモデルロードが走るため 300 秒
- **3秒ACK**: Slack の Lazy Listeners で即 ack → 非同期再 invoke で推論、の流れは維持
- **アーキ**: arm64(Graviton) 既定。x86_64 にする場合は `serverless.yml` の
  `architecture` と `ecr.images.*.platform` を `x86_64` / `linux/amd64` に変更
- `model/`（GGUF本体）は容量が大きいので Git 管理外（`.gitignore`）

## Function URL 版メモ（API Gateway を使わない選択肢）

現状は **API Gateway（REST API）** で Slack の受け口を公開しているっしゅ。
API Gateway を挟まず **Lambda Function URL** で直接公開することもできるが、
**Serverless Framework だけでは完結できない**ので注意っしゅ。切り替えるときの勘所：

### 切り替え方
`serverless.yml` の `functions.slack` を以下に変更：
```yaml
    # events: の代わりに
    url: true
```

### ⚠️ ハマりどころ（重要）
- 公開 Function URL には **2つ**のリソースポリシー文が要る：
  1. `lambda:InvokeFunctionUrl`（URL到達）
  2. `lambda:InvokeFunction`（関数実行）
- **SF v3.40 の `url: true` は ① しか作らない**ため、そのままだと
  `403 Forbidden（Function URL authorization issues）` になる。
- ② の最小権限版（条件 `lambda:InvokedViaFunctionUrl=true`）は
  **CloudFormation でも `aws lambda add-permission` でも作れず、マネジメントコンソール限定**っしゅ
  （`AWS::Lambda::Permission` に汎用 Condition プロパティが無い）。
- IaC で回避するなら `resources:` に
  `AWS::Lambda::Permission`（Action=`lambda:InvokeFunction`, Principal=`*`, 条件なし）を追加する
  ＝動くが「任意の AWS アカウントから直接 invoke 可」と広めになるトレードオフ。

### なぜ今 API Gateway を選んでいるか
- コスト差は Slack 用途では誤差（HTTP API $1 / REST API $3.5 / 100万req、Function URL $0）。
- **API Gateway は SF だけで権限まで自動・一発で動く**（上記の権限ハマりが無い）。
- スロットリング標準装備・WAF/カスタムドメイン対応など、公開エンドポイントとして有利。
- Function URL の強み（>30秒の同期応答・ストリーミング）は Slack の Lazy Listeners 構成では不要。

→ 詳細は git 履歴や開発メモ参照。**長時間同期/ストリーミングが必要になったら Function URL を再検討**するっしゅ。

## デバッグログ

- **ログレベル**: 環境変数 `LOG_LEVEL`（`DEBUG`/`INFO`/`WARNING`/`ERROR`）
- **デフォルト**: `INFO`
- **デバッグ時**: `.env` に `LOG_LEVEL=DEBUG` を追加
