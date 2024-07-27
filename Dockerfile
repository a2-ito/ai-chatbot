FROM amazonlinux:2023

ENV NODE_VERSION 20.13.0

RUN dnf update
RUN dnf install -y git tar make vim gcc zlib-devel bzip2-devel readline-devel sqlite sqlite-devel openssl-devel tk-devel libffi-devel xz-devel

RUN touch /root/.bashrc

ADD https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.3/install.sh .
RUN bash install.sh
RUN source /root/.bashrc && nvm install v$NODE_VERSION

RUN source /root/.bashrc && npm install -g serverless@3.39.0

COPY . /app

RUN mkdir /root/.aws
RUN touch /root/.aws/config

# RUN source /root/.bashrc && cd /app/gemini && serverless plugin install -n serverless-offline
# RUN source /root/.bashrc && cd /app/gemini && serverless plugin install -n serverless-python-requirements

RUN curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
# RUN curl https://pyenv.run | bash

# RUN source /root/.bashrc && pyenv install 3.11.5
