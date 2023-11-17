FROM python:3.9-slim-bullseye

COPY ./ /passivbot/

WORKDIR /passivbot

RUN apt update && apt install -y \
    tmux \
    tmuxp
    
RUN pip install -r requirements_liveonly.txt
