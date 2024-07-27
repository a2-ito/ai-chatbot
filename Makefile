APP									=	ai-chat
AWS_PROFILE					= XXX

export
AWS_SDK_LOAD_CONFIG := 1

all: deploy-gemini

offline:
	sls offline --noPrependStageInUrl

deploy:
	AWS_SDK_LOAD_CONFIG=1 sls deploy --aws-profile $(AWS_PROFILE)

docker-run:
	docker run -it bash

docker-build:
	docker build -t $(APP)-env .
