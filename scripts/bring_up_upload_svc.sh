#!/bin/bash
set -x

cp -f config/.upload_env ../../insights-upload/docker/.env

cd ../../insights-upload/docker/
. .env
pipenv install --dev
pipenv run docker-compose up
