=============
Yupana README
=============

|license| |Build Status| |codecov| |Updates| |Python 3| |Docs|

~~~~~
About
~~~~~

Full documentation is available through `readthedocs`_.

Getting Started
===============

This is a Python project developed using Python 3.6. Make sure you have at least this version installed.

Development
===========

To get started developing against Yupana first clone a local copy of the git repository. ::

    git clone https://github.com/quipucords/yupana

Developing inside a virtual environment is recommended. A Pipfile is provided. Pipenv is recommended for combining virtual environment (virtualenv) and dependency management (pip). To install pipenv, use pip ::

    pip3 install pipenv

Then project dependencies and a virtual environment can be created using ::

    pipenv install --dev

To activate the virtual environment run ::

    pipenv shell

Configuration
^^^^^^^^^^^^^

This project is developed using the Django web framework. Many configuration settings can be read in from a `.env` file. An example file `.env.example` is provided in the repository. To use the defaults simply ::

    cp .env.example .env


Modify as you see fit.


Server
^^^^^^

To run a local dev Django server you can use ::

    make server-init
    make serve

Run Server with gunicorn
^^^^^^^^^^^^^^^^^^^^^^^^

To run a local gunicorn server with yupana do the following::

    make server-init
    gunicorn config.wsgi -c ./yupana/config/gunicorn.py --chdir=./yupana/


Preferred Environment
---------------------

Please refer to `Working with Openshift`_.

Configuration
^^^^^^^^^^^^^

This project is developed using the Django web framework. Many configuration settings can be read in from a ``.env`` file. An example file ``.env.example`` is provided in the repository. To use the defaults simply ::

    cp .env.example .env


Modify as you see fit.

Database
^^^^^^^^

PostgreSQL is used as the database backend for Yupana. A docker-compose file is provided for creating a local database container. If modifications were made to the .env file the docker-compose file will need to be modified to ensure matching database credentials. Several commands are available for interacting with the database. ::

    # This will launch a Postgres container
    make start-db

    # This will run Django's migrations against the database
    make server-migrate

    # This will stop and remove a currently running database and run the above commands. If this command fails, try running each command it combines separately, using ``docker ps`` in between to track the existence of the db ::
    make reinitdb

Assuming the default .env file values are used, to access the database directly using psql run ::

    psql postgres -U postgres -h localhost -p 15432

There is a known limitation with docker-compose and Linux environments with SELinux enabled. You may see the following error during the postgres container deployment::

    "mkdir: cannot create directory '/var/lib/pgsql/data/userdata': Permission denied" can be resolved by granting ./pg_data ownership permissions to uid:26 (postgres user in centos/postgresql-96-centos7)

If a docker container running Postgres is not feasible, it is possible to run Postgres locally as documented in the Postgres tutorial_. The default port for local Postgres installations is ``5432``. Make sure to modify the `.env` file accordingly. To initialize the database run ::

    make server-migrate


API Documentation Generation
----------------------------

To generate and host the API documentation locally you need to `Install APIDoc`_.

Generate the project API documentation by running the following command ::

   `make gen-apidoc`

In order to host the docs locally you need to collect the static files ::

   `make server-static`

Now start the server as described above and point your browser to
**http://127.0.0.1:8000/apidoc/index.html**

Testing and Linting
-------------------

Yupana uses tox to standardize the environment used when running tests. Essentially, tox manages its own virtual environment and a copy of required dependencies to run tests. To ensure a clean tox environment run ::

    tox -r

This will rebuild the tox virtual env and then run all tests.

To run unit tests specifically::

    tox -e py36

To lint the code base ::

    tox -e lint


.. _readthedocs: https://yupana.readthedocs.io/en/latest/
.. |license| image:: https://img.shields.io/github/license/quipucords/yupana.svg
.. |Updates| image:: https://pyup.io/repos/github/quipucords/yupana/shield.svg
   :target: https://pyup.io/repos/github/quipucords/yupana/
.. |Python 3| image:: https://pyup.io/repos/github/quipucords/yupana/python-3-shield.svg
.. _`Install APIDoc`: http://apidocjs.com/#install
.. _`Working with Openshift`: https://yupana.readthedocs.io/en/latest/openshift.html
.. _tutorial: https://www.postgresql.org/docs/10/static/tutorial-start.html
