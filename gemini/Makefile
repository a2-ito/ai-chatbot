APP									=	ai-chat
AWS_PROFILE					= XXX

export
AWS_SDK_LOAD_CONFIG := 1

all: deploy

offline:
	sls offline --noPrependStageInUrl

deploy:
	AWS_SDK_LOAD_CONFIG=1 sls deploy --aws-profile $(AWS_PROFILE)
