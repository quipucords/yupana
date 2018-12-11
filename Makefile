PYTHON=$(shell which python)
IMAGE_NAME=yupana-centos7

TOPDIR=$(shell pwd)
PYDIR=yupana
APIDOC=apidoc
STATIC=staticfiles
OC_SOURCE=registry.access.redhat.com/openshift3/ose
OC_VERSION=v3.9
OC_DATA_DIR=${HOME}/.oc/openshift.local.data
OPENSHIFT_PROJECT='yupana'
OPENSHIFT_TEMPLATE_PATH='openshift/yupana-template.yaml'
TEMPLATE='yupana-template'
CODE_REPO='https://github.com/quipucords/yupana.git'
REPO_BRANCH='master'
EMAIL_SERVICE_PASSWORD=$EMAIL_SERVICE_PASSWORD
PGSQL_VERSION=9.6

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
	@echo "server-migrate           run migrations against database"
	@echo "serve                    run the Django server locally"
	@echo "validate-swagger         to run swagger-cli validation"
	@echo "unittest                 run the unit tests"
	@echo "test-coverage            run the test coverage"
	@echo "requirements             create requirements.txt for readthedocs"
	@echo "gen-apidoc               create api documentation"
	@echo "server-static            collect static files to host"
	@echo "start-db                 start postgres db"
	@echo "clean-db                 remove postgres db"
	@echo "reinit-db                remove db and start a new one"
	@echo ""
	@echo "--- Commands using an OpenShift Cluster ---"
	@echo "oc-clean                 stop openshift cluster & remove local config data"
	@echo "oc-up                    initialize an openshift cluster"
	@echo "oc-up-dev                run app in openshift cluster"
	@echo "oc-down                  stop app & openshift cluster"
	@echo "oc-create-yupana         create the Yupana app in an initialized openshift cluster"
	@echo "oc-login-admin           login to openshift as admin"
	@echo "oc-login-developer       login to openshift as developer"
	@echo "oc-server-migrate        run migrations"


clean:
	git clean -fdx -e .idea/ -e *env/ $(PYDIR)/db.sqlite3
	rm -rf yupana/static

gen-apidoc:
	rm -fr $(PYDIR)/$(STATIC)/
	apidoc -i $(PYDIR) -o $(APIDOC)

html:
	@cd docs; $(MAKE) html

lint:
	tox -elint

collect-static:
	$(PYTHON) $(PYDIR)/manage.py collectstatic --no-input

server-migrate:
	DJANGO_READ_DOT_ENV_FILE=True $(PYTHON) $(PYDIR)/manage.py migrate -v 3

serve:
	DJANGO_READ_DOT_ENV_FILE=True $(PYTHON) $(PYDIR)/manage.py runserver 0.0.0.0:8001

server-static:
	mkdir -p ./yupana/static/client
	$(PYTHON) yupana/manage.py collectstatic --settings config.settings.local --no-input

server-init: server-migrate server-static

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

oc-up:
	oc cluster up \
		--image=$(OC_SOURCE) \
		--version=$(OC_VERSION) \
		--host-data-dir=$(OC_DATA_DIR) \
		--use-existing-config=true
	sleep 60

oc-down:
	oc cluster down

oc-clean: oc-down
	$(PREFIX) rm -rf $(OC_DATA_DIR)

oc-login-admin:
	oc login -u system:admin

oc-login-developer:
	oc login -u developer -p developer --insecure-skip-tls-verify

oc-project:
	oc new-project ${OPENSHIFT_PROJECT}
	oc project ${OPENSHIFT_PROJECT}

oc-new-app:
	oc new-app --template ${OPENSHIFT_PROJECT}/${TEMPLATE} \
		--param NAMESPACE=${OPENSHIFT_PROJECT} \
		--param SOURCE_REPOSITORY_URL=${CODE_REPO} \
        --param SOURCE_REPOSITORY_REF=${REPO_BRANCH}

oc-apply:
	oc apply -f ${OPENSHIFT_TEMPLATE_PATH}

oc-up-dev: oc-up oc-project oc-apply oc-new-app

oc-create-all: oc-login-developer oc-project oc-create-tags oc-apply oc-new-app

oc-create-yupana: oc-login-developer oc-project oc-apply oc-new-app

oc-create-tags:
	oc get istag postgresql:$(PGSQL_VERSION) || oc create istag postgresql:$(PGSQL_VERSION) --from-image=centos/postgresql-96-centos7

oc-create-db:
	oc process openshift//postgresql-persistent \
		-p NAMESPACE=yupana \
		-p POSTGRESQL_USER=yupanaadmin \
		-p POSTGRESQL_PASSWORD=admin123 \
		-p POSTGRESQL_DATABASE=yupana \
		-p POSTGRESQL_VERSION=$(PGSQL_VERSION) \
		-p DATABASE_SERVICE_NAME=yupana-pgsql \
	| oc create -f -

oc-server-migrate: oc-forward-ports
	sleep 3
	DJANGO_READ_DOT_ENV_FILE=True $(PYTHON) $(PYDIR)/manage.py migrate
	make oc-stop-forwarding-ports

oc-stop-forwarding-ports:
	kill -HUP $$(ps -eo pid,command | grep "oc port-forward" | grep -v grep | awk '{print $$1}')

oc-forward-ports:
	-make oc-stop-forwarding-ports 2>/dev/null
	oc port-forward $$(oc get pods -o jsonpath='{.items[*].metadata.name}' -l name=yupana-pgsql) 15432:5432 >/dev/null 2>&1 &

clean-db:
	$(PREFIX) rm -rf $(TOPDIR)/pg_data
	make compose-down

start-db:
	docker-compose up -d db

compose-down:
	docker-compose down

reinit-db: compose-down clean-db start-db server-migrate

oc-up-db: oc-up oc-create-db

serve-with-oc: oc-forward-ports
	sleep 3
	DJANGO_READ_DOT_ENV_FILE=True $(PYTHON) $(PYDIR)/manage.py runserver
	make oc-stop-forwarding-ports
