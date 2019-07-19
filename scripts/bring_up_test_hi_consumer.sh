#!/bin/bash
set - x

cd test

. .env
pipenv run python consumer.py
