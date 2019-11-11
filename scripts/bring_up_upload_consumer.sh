#!/bin/bash
set -x

cd ../../insights-ingress-go/
. .env
docker-compose exec kafka kafka-console-consumer --topic=platform.upload.qpc --bootstrap-server=localhost:29092