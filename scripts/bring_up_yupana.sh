#!/bin/bash
set -x

cd config/
. .yupana_env
cd ../..

docker-compose up -d
./scripts/countdown.sh 'Waiting for yupana db to be ready.' 30 'Yupana db is ready'
pipenv run make server-init
pipenv run make serve
