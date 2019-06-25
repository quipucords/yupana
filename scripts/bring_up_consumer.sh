#!/bin/bash
set -x

./countdown.sh 'Waiting for file upload services to startup' 30 'Services are ready!'
cd ../../insights-upload/docker/
. .env
docker-compose exec kafka kafka-console-consumer --topic=platform.upload.qpc --bootstrap-server=localhost:29092