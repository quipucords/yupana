#!/bin/bash
set -x

upload_dir_path='../../insights-ingress-go'
cp -f config/.upload_env $upload_dir_path/.env
cd $upload_dir_path
. .env
pipenv install --dev
pipenv run docker-compose up --build
