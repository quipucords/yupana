#!/bin/bash
set -x

cp -f config/.host_env ../../insights-host-inventory/.env
cd ../../insights-host-inventory/
. .env

pipenv install --dev
../yupana/scripts/countdown.sh 'Waiting for host inventory db to be ready.' 15 'Host inventory db is ready'
pipenv run python manage.py db upgrade
pipenv run python run_gunicorn.py
