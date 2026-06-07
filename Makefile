include ./.env

APP=ai-chat

export
AWS_SDK_LOAD_CONFIG := 1

all: deploy

offline:
	sls offline --noPrependStageInUrl

deploy:
	AWS_SDK_LOAD_CONFIG=1 npx sls deploy --aws-profile $(AWS_PROFILE)

remove:
	AWS_SDK_LOAD_CONFIG=1 npx sls remove --aws-profile $(AWS_PROFILE)

docker-run:
	docker run -it bash

build:
	docker build -t $(APP)-env .
