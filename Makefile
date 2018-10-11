PYTHON	= $(shell which python)
IMAGE_NAME = yupana-centos7

TOPDIR  = $(shell pwd)
PYDIR	= yupana
APIDOC = apidoc
STATIC = staticfiles

OS := $(shell uname)
ifeq ($(OS),Darwin)
	PREFIX	=
else
	PREFIX	= sudo
endif

help:
	@echo "Please use \`make <target>' where <target> is one of:"
	@echo ""
	@echo "--- General Commands ---"
	@echo "clean                    clean the project directory of any scratch files, bytecode, logs, etc."
	@echo "help                     show this message"
	@echo ""
	@echo "--- Commands using local services ---"
	@echo "run-migrations           run migrations against database"
	@echo "serve                    run the Django server locally"
	@echo "validate-swagger         to run swagger-cli validation"
	@echo "unittest                 run the unit tests"
	@echo "test-coverage            run the test coverage"
	@echo "requirements             create requirements.txt for readthedocs"
	@echo "gen-apidoc               create api documentation"
	@echo "server-static            collect static files to host"

clean:
	git clean -fdx -e .idea/ -e *env/ $(PYDIR)/db.sqlite3
	rm -rf yupana/static

gen-apidoc:
	rm -fr $(PYDIR)/$(STATIC)/
	apidoc -i $(PYDIR) -o $(APIDOC)

collect-static:
	$(PYTHON) $(PYDIR)/manage.py collectstatic --no-input

run-migrations:
	DJANGO_READ_DOT_ENV_FILE=True $(PYTHON) $(PYDIR)/manage.py migrate -v 3

serve:
	DJANGO_READ_DOT_ENV_FILE=True $(PYTHON) $(PYDIR)/manage.py runserver

server-static:
	mkdir -p ./yupana/static/client
	$(PYTHON) yupana/manage.py collectstatic --settings config.settings.local --no-input

server-init: run-migrations server-static

unittest:
	$(PYTHON) $(PYDIR)/manage.py test $(PYDIR) -v 2

test-coverage:
	tox -e py36 --

requirements:
	pipenv lock
	pipenv lock -r > docs/rtd_requirements.txt

validate-swagger:
	npm install swagger-cli
	node_modules/swagger-cli/bin/swagger-cli.js validate docs/swagger.yml

.PHONY: build
build:
	docker build -t $(IMAGE_NAME) .

.PHONY: test
test:
	docker build -t $(IMAGE_NAME)-candidate .
	IMAGE_NAME=$(IMAGE_NAME)-candidate test/run