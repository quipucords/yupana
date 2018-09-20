PYTHON	= $(shell which python)

TOPDIR  = $(shell pwd)
PYDIR	= yupana

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
	@echo "unittest                 run the unit tests"
	@echo "test-coverage            run the test coverage"
	@echo "requirements             create requirements.txt for readthedocs"

clean:
	git clean -fdx -e .idea/ -e *env/ $(PYDIR)/db.sqlite3

run-migrations:
	DJANGO_READ_DOT_ENV_FILE=True $(PYTHON) $(PYDIR)/manage.py migrate

serve:
	DJANGO_READ_DOT_ENV_FILE=True $(PYTHON) $(PYDIR)/manage.py runserver

unittest:
	$(PYTHON) $(PYDIR)/manage.py test $(PYDIR) -v 2

test-coverage:
	tox -e py36 --

requirements:
	pipenv lock
	pipenv lock -r > docs/rtd_requirements.txt
