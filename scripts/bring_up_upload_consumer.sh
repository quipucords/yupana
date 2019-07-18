#!/bin/bash
set -x

cd ../../insights-upload/docker/
. .env
docker-compose exec kafka kafka-console-consumer --topic=platform.upload.qpc --bootstrap-server=localhost:29092