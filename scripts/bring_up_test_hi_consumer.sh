#!/bin/bash
set - x

cd test_host_consumer

. .env
pipenv run python consumer.py
