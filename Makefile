-include ./.env

APP        ?= ai-chat
IMAGE_NAME ?= ai-chat
TAG        ?= local
PORT       ?= 9000
CONTAINER_NAME ?= ai-chat-local
PROMPT     ?= 日本語で自己紹介してください。
# arm64 = Apple Silicon でネイティブビルド（QEMU不要）。
# x86_64 Lambda 向けにする場合は PLATFORM=linux/amd64（serverless.yml も合わせる）。
PLATFORM   ?= linux/arm64

# デプロイ済み Lambda への invoke-remote 用（${service}-${stage}-${function}）。
FUNCTION_NAME ?= ai-chat-dev-slack
AWS_REGION    ?= us-east-1

# モデルは models.env で選択（使うモデルだけコメントイン）。
# CLI で一時上書きも可: make switch HF_REPO=... HF_FILE=...
export APP IMAGE_NAME TAG PORT CONTAINER_NAME HF_REPO HF_FILE PLATFORM
export AWS_SDK_LOAD_CONFIG := 1

MODEL_FILE := model/model.gguf
INVOKE_URL := http://localhost:$(PORT)/2015-03-31/functions/function/invocations

.DEFAULT_GOAL := help

# ---- ヘルプ ----------------------------------------------------------------
.PHONY: help
help: ## このヘルプを表示
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# ---- モデル ----------------------------------------------------------------
$(MODEL_FILE):
	bash scripts/download_model.sh

.PHONY: download
download: $(MODEL_FILE) ## GGUFモデルをダウンロード（model/model.gguf）

.PHONY: which-model
which-model: ## ダウンロード済みモデルの情報を表示
	@if [ -f $(MODEL_FILE) ]; then \
		printf 'file : %s\nsize : %s\n' "$(MODEL_FILE)" "$$(du -h $(MODEL_FILE) | cut -f1)"; \
	else \
		echo "未ダウンロード（make download で取得）"; \
	fi

.PHONY: clean-model
clean-model: ## ダウンロード済みモデルを削除
	-rm -f $(MODEL_FILE)

.PHONY: switch
switch: clean-model download ## models.env の選択を反映（既存削除→再取得）

# ---- ローカル検証（RIE） ---------------------------------------------------
.PHONY: build
build: download ## ローカル用コンテナイメージをビルド
	bash scripts/build.sh

.PHONY: run
run: ## ローカル起動＋テスト推論（PROMPT="..." で指定可）
	bash scripts/run_local.sh "$(PROMPT)"

.PHONY: invoke
invoke: ## 起動中コンテナへ手動 invoke（PROMPT="..." で指定可）
	@curl -s $(INVOKE_URL) \
		-d "$$(printf '{"prompt": %s, "max_tokens": 256}' "$$(printf '%s' '$(PROMPT)' | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")" \
		| python3 -m json.tool --no-ensure-ascii

.PHONY: logs
logs: ## 起動中コンテナのログを追跡
	docker logs -f $(CONTAINER_NAME)

.PHONY: stop
stop: ## 起動中コンテナを停止・削除
	-docker rm -f $(CONTAINER_NAME)

.PHONY: clean
clean: stop ## コンテナ削除＋ローカルイメージ削除
	-docker rmi $(IMAGE_NAME):$(TAG)

# ---- デプロイ（Serverless Framework / コンテナモード） ---------------------
# Serverless Framework v3 とプラグインをローカルに導入。
# package*.json は .gitignore 対象のため、CI と同様に直接インストールする。
# （`npx sls` は未インストール時に無関係な npm パッケージ `sls` を拾うため使わない）
node_modules/.bin/serverless:
	npm install --legacy-peer-deps serverless@3 serverless-better-credentials

.PHONY: setup
setup: node_modules/.bin/serverless ## Serverless Framework とプラグインをローカル導入

.PHONY: deploy
deploy: download setup ## ECR push＆Lambdaデプロイ（事前にモデルDL & SF導入）
	AWS_SDK_LOAD_CONFIG=1 npx serverless deploy --aws-profile $(AWS_PROFILE)

.PHONY: remove
remove: setup ## デプロイ済みスタックを削除
	AWS_SDK_LOAD_CONFIG=1 npx serverless remove --aws-profile $(AWS_PROFILE)

.PHONY: invoke-remote
invoke-remote: ## デプロイ済みLambdaを直接invoke（PROMPT="..." / 認証済みAWSユーザ用）
	@aws lambda invoke --function-name $(FUNCTION_NAME) --region $(AWS_REGION) --profile $(AWS_PROFILE) \
		--cli-binary-format raw-in-base64-out --cli-read-timeout 300 \
		--payload "$$(printf '{"prompt": %s, "max_tokens": 256}' "$$(printf '%s' '$(PROMPT)' | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")" \
		/tmp/ai-chat-remote.json >/dev/null \
		&& python3 -c "import json; d=json.load(open('/tmp/ai-chat-remote.json')); b=json.loads(d['body']) if isinstance(d,dict) and 'body' in d else d; print(b.get('output', b) if isinstance(b,dict) else b)"
