PYTHON=$(shell which python)
IMAGE_NAME=yupana-centos7
.PHONY: build

TOPDIR=$(shell pwd)
PYDIR=yupana
APIDOC=apidoc
STATIC=staticfiles

# OC Params
OC_TEMPLATE_DIR = $(TOPDIR)/openshift
OC_PARAM_DIR = $(OC_TEMPLATE_DIR)/parameters

# required OpenShift template parameters
NAME = 'yupana'
NAMESPACE = 'yupana'

# OC dev variables
OC_SOURCE=registry.access.redhat.com/openshift3/ose
OC_VERSION=v3.9
OC_DATA_DIR=${HOME}/.oc/openshift.local.data

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
	@echo "lint                     to run linters on code"
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
	@echo "oc-down                  stop app & openshift cluster"
	@echo "oc-create-secret         create secret in an initialized openshift cluster"
	@echo "oc-login-admin           login to openshift as admin"
	@echo "oc-login-developer       login to openshift as developer"
	@echo "oc-server-migrate        run migrations"
	@echo "oc-delete-yupana         delete the yupana project, app, and data"
	@echo "oc-delete-yupana-data    delete the yupana app and data"
	@echo "oc-refresh               apply template changes to openshift dedicated"
	@echo ""
	@echo "--- Commands to upload data to Insights ---"
	@echo "sample-data                                 ready sample data for upload to Insights"
	@echo "custom-data file=<path/to/file>             ready given data for upload to Insights"
	@echo "upload-data file=<path/to/file>             upload data to Insights"
	@echo "create-report hosts=<number>                generates a report with x amount of hosts"
	@echo ""
	@echo "--- Commands for local development ---"
	@echo "local-dev-up                                bring up yupana with all required services"
	@echo "local-dev-down                              bring down yupana with all required services"
	@echo "local-upload-data file=<path/to/file>       upload data to local file upload service for yupana processing"
	@echo ""

clean:
	git clean -fdx -e .idea/ -e *env/ $(PYDIR)/db.sqlite3
	rm -rf yupana/static
	rm -rf temp/

gen-apidoc:
	rm -fr $(PYDIR)/$(STATIC)/
	apidoc -i $(PYDIR) -o $(APIDOC)

html:
	@cd docs; $(MAKE) html

lint:
	tox -elint

collect-static:
	$(PYTHON) $(PYDIR)/manage.py collectstatic --no-input

server-makemigrations:
	$(PYTHON) $(PYDIR)/manage.py makemigrations api --settings config.settings.local

server-migrate:
	DJANGO_READ_DOT_ENV_FILE=True $(PYTHON) $(PYDIR)/manage.py migrate -v 3

serve:
	DJANGO_READ_DOT_ENV_FILE=True $(PYTHON) $(PYDIR)/manage.py runserver 127.0.0.1:8001

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

build:
	docker build -t $(IMAGE_NAME) .

clean-db:
	$(PREFIX) rm -rf $(TOPDIR)/pg_data
	make compose-down

start-db:
	docker-compose up -d db

compose-down:
	docker-compose down

reinit-db: compose-down clean-db start-db server-migrate

sample-data:
	mkdir -p temp/reports
	mkdir -p temp/old_reports_temp
	tar -xvzf sample.tar.gz -C temp/old_reports_temp
	python scripts/change_uuids.py
	@NEW_FILENAME="sample_data_ready_$(shell date +%s).tar.gz"; \
	cd temp; COPYFILE_DISABLE=1 tar -zcvf $$NEW_FILENAME reports; \
	echo ""; \
	echo "The updated report was written to" temp/$$NEW_FILENAME; \
	echo ""; \
	rm -rf reports; \
	rm -rf old_reports_temp

custom-data:
	mkdir -p temp/reports
	mkdir -p temp/old_reports_temp
	tar -xvzf $(file) -C temp/old_reports_temp
	python scripts/change_uuids.py
	@NEW_FILENAME="sample_data_ready_$(shell date +%s).tar.gz"; \
	cd temp; COPYFILE_DISABLE=1 tar -zcvf $$NEW_FILENAME reports; \
	echo ""; \
	echo "The updated report was written to" temp/$$NEW_FILENAME; \
	echo ""; \
	rm -rf reports; \
	rm -rf old_reports_temp

upload-data:
	curl -vvvv -H "x-rh-identity: $(shell echo '{"identity": {"account_number": $(RH_ACCOUNT_NUMBER), "internal": {"org_id": $(RH_ORG_ID)}}}' | base64)" \
		-F "file=@$(file);type=application/vnd.redhat.qpc.tar+tgz" \
		-H "x-rh-insights-request-id: 52df9f748eabcfea" \
		$(FILE_UPLOAD_URL) \
		-u $(RH_USERNAME):$(RH_PASSWORD)

create-report:
	mkdir -p temp/reports
	$(PYTHON) scripts/create_report.py hosts=$(hosts)
	@NEW_FILENAME="report_$(hosts)h_$(shell date +%s).tar.gz"; \
	cd temp; COPYFILE_DISABLE=1 tar -zcvf $$NEW_FILENAME reports; \
	echo ""; \
	echo "The updated report was written to" temp/$$NEW_FILENAME; \
	echo ""; \
	rm -rf reports; \

local-dev-up:
	./scripts/bring_up_all.sh
	clear

local-dev-down:
	osascript -e 'quit app "iTerm"' | true
	cd ../insights-upload/docker/;docker-compose down
	docker-compose down
	sudo lsof -t -i tcp:8081 | xargs kill -9

local-upload-data:
	curl -vvvv -H "x-rh-identity: eyJpZGVudGl0eSI6IHsiYWNjb3VudF9udW1iZXIiOiAiMTIzNDUiLCAiaW50ZXJuYWwiOiB7Im9yZ19pZCI6ICI1NDMyMSJ9fX0=" \
		-F "file=@$(file);type=application/vnd.redhat.qpc.tar+tgz" \
		-H "x-rh-insights-request-id: 52df9f748eabcfea" \
		localhost:8080/api/ingress/v1/upload

# Local commands for working with OpenShift
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
	oc new-project ${NAMESPACE}
	oc project ${NAMESPACE}

oc-delete-yupana-data:
	oc delete all -l app=yupana
	oc delete persistentvolumeclaim yupana-db
	oc delete configmaps yupana-env
	oc delete configmaps yupana-db
	oc delete configmaps yupana-app
	oc delete configmaps yupana-messaging
	oc delete secret yupana-secret
	oc delete secret yupana-db

oc-delete-project:
	oc delete project yupana

oc-delete-yupana: oc-delete-yupana-data oc-delete-project

oc-server-migrate: oc-forward-ports
	sleep 3
	DJANGO_READ_DOT_ENV_FILE=True $(PYTHON) $(PYDIR)/manage.py migrate
	make oc-stop-forwarding-ports

# internal command used by server-migrate & serve with oc
oc-stop-forwarding-ports:
	kill -HUP $$(ps -eo pid,command | grep "oc port-forward" | grep -v grep | awk '{print $$1}')

# internal command used by server-migrate & serve with oc
oc-forward-ports:
	-make oc-stop-forwarding-ports 2>/dev/null
	oc port-forward $$(oc get pods -o jsonpath='{.items[*].metadata.name}' -l name=yupana-db) 15432:5432 >/dev/null 2>&1 &

serve-with-oc: oc-forward-ports
	sleep 3
	DJANGO_READ_DOT_ENV_FILE=True $(PYTHON) $(PYDIR)/manage.py runserver
	make oc-stop-forwarding-ports


oc-create-secret: OC_OBJECT = 'secret -l app=$(NAME)'
oc-create-secret: OC_PARAMETER_FILE = secret.env
oc-create-secret: OC_TEMPLATE_FILE = secret.yaml
oc-create-secret: OC_PARAMS = OC_OBJECT=$(OC_OBJECT) OC_PARAMETER_FILE=$(OC_PARAMETER_FILE) OC_TEMPLATE_FILE=$(OC_TEMPLATE_FILE) NAMESPACE=$(NAMESPACE)
oc-create-secret:
	$(OC_PARAMS) $(MAKE) __oc-apply-object
	$(OC_PARAMS) $(MAKE) __oc-create-object

##################################
### Internal openshift targets ###
##################################

__oc-create-project:
	@if [[ ! $$(oc get -o name project/$(NAMESPACE) 2>/dev/null) ]]; then \
		oc new-project $(NAMESPACE) ;\
	fi

# if object doesn't already exist,
# create it from the provided template and parameters
__oc-create-object: __oc-create-project
	@if [[ $$(oc get -o name $(OC_OBJECT) 2>&1) == '' ]] || \
	[[ $$(oc get -o name $(OC_OBJECT) 2>&1 | grep 'not found') ]]; then \
		if [ -f $(OC_PARAM_DIR)/$(OC_PARAMETER_FILE) ]; then \
			oc process -f $(OC_TEMPLATE_DIR)/$(OC_TEMPLATE_FILE) \
				--param-file=$(OC_PARAM_DIR)/$(OC_PARAMETER_FILE) \
			| oc create --save-config=True -n $(NAMESPACE) -f - 2>&1 | grep -v "already exists" || /usr/bin/true ;\
		else \
			oc process -f $(OC_TEMPLATE_DIR)/$(OC_TEMPLATE_FILE) \
				$(foreach PARAM, $(OC_PARAMETERS), -p $(PARAM)) \
			| oc create --save-config=True -n $(NAMESPACE) -f - 2>&1 | grep -v "already exists" || /usr/bin/true ;\
		fi ;\
	fi

 __oc-apply-object: __oc-create-project
	@if [[ $$(oc get -o name $(OC_OBJECT) 2>&1) != '' ]] || \
	[[ $$(oc get -o name $(OC_OBJECT) 2>&1 | grep -v 'not found') ]]; then \
		echo "WARNING: Resources matching 'oc get $(OC_OBJECT)' exists. Updating template. Skipping object creation." ;\
		if [ -f $(OC_PARAM_DIR)/$(OC_PARAMETER_FILE) ]; then \
			oc process -f $(OC_TEMPLATE_DIR)/$(OC_TEMPLATE_FILE) \
				--param-file=$(OC_PARAM_DIR)/$(OC_PARAMETER_FILE) \
			| oc apply -f - ;\
		else \
			oc process -f $(OC_TEMPLATE_DIR)/$(OC_TEMPLATE_FILE) \
				$(foreach PARAM, $(OC_PARAMETERS), -p $(PARAM)) \
			| oc apply -f - ;\
		fi ;\
	fi

#
# Phony targets
#
.PHONY: docs __oc-create-object __oc-create-project __oc-apply-object