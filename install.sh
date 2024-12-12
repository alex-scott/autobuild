#!/bin/bash -ex
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd $dir

#sudo apt install python3.8 python3.8-venv

#adduser --group --system autobuild
docker build -t cgicentral/autobuild -f docker/Dockerfile.autobuild docker/

mkdir -p ./git ./scripts ./workspace
if [ ! -f .env ]; then
  cp .env-dist .env
fi

/usr/bin/python3 -m venv ./venv
./venv/bin/python3 -m pip install -r requirements.txt

## for running webhooks, not necessary for local devs
#sudo snap install webhook
#sudo apt install daemon
sudo apt install inotify-tools

