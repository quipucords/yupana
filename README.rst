=============
Yupana README
=============

|license| |Updates| |Python 3|

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

Database
^^^^^^^^

PostgreSQL is used as the database backend for Yupana. A docker-compose file is provided for creating a local database container. If modifications were made to the .env file the docker-compose file will need to be modified to ensure matching database credentials. Several commands are available for interacting with the database. ::

    # This will launch a Postgres container
    make start-db

    # This will run Django's migrations against the database
    make server-migrate

    # This will stop and remove a currently running database and run the above commands. If this command fails, try running each command it combines separately, using ``docker ps`` in between to track the existence of the db ::
    make reinit-db

Assuming the default .env file values are used, to access the database directly using psql run ::

    psql postgres -U postgres -h localhost -p 15432

There is a known limitation with docker-compose and Linux environments with SELinux enabled. You may see the following error during the postgres container deployment::

    "mkdir: cannot create directory '/var/lib/pgsql/data/userdata': Permission denied" can be resolved by granting ./pg_data ownership permissions to uid:26 (postgres user in centos/postgresql-96-centos7)
  
If this error is encountered, the following command can be used to grant ``pg_data`` ownership permissions to uid:26 as the error suggests ::

  setfacl -m u:26:-wx ./pg_data/


If a docker container running Postgres is not feasible, it is possible to run Postgres locally as documented in the Postgres tutorial_. The default port for local Postgres installations is ``5432``. Make sure to modify the `.env` file accordingly. To initialize the database run ::

    make server-migrate


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
^^^^^^^^^^^^^^^^^^^^^

Please refer to `Working with Openshift`_.


API Documentation Generation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To generate and host the API documentation locally you need to `Install APIDoc`_.

Generate the project API documentation by running the following command ::

   `make gen-apidoc`

In order to host the docs locally you need to collect the static files ::

   `make server-static`

Now start the server as described above and point your browser to
**http://127.0.0.1:8001/apidoc/index.html**

Testing and Linting
^^^^^^^^^^^^^^^^^^^

Yupana uses tox to standardize the environment used when running tests. Essentially, tox manages its own virtual environment and a copy of required dependencies to run tests. To ensure a clean tox environment run ::

    tox -r

This will rebuild the tox virtual env and then run all tests.

To run unit tests specifically::

    tox -e py36

To lint the code base ::

    tox -e lint

Formatting Data for Yupana
^^^^^^^^^^^^^^^^^^^^^^^^^^
Data sent to Yupana should include the following sections in given format (JSON):
    1. Metadata. Data should include metadata section which contains metadata information about the report and sources, like an example below: ::
       
        {
            "report_id": "05f373dd-e20e-4866-b2a4-9b523acfeb6d",
            "host_inventory_api_version": "1.0",
            "source": "qpc",
            "source_metadata": {
                "report_platform_id": "05f373dd-e20e-4866-b2a4-9b523acfeb6d",
                "report_type": "insights",
                "report_version": "1.0.0.7858056",
                "qpc_server_report_id": 2,
                "qpc_server_version": "1.0.0.7858056",
                "qpc_server_id": "56deb667-8ddd-4647-b1b7-e36e614871d0"
            },
            "report_slices": {
                "2dd60c11-ee5b-4ddc-8b75-d8d34de86a34": {
                    "number_hosts": 1
                },
                "eb45725b-165a-44d9-ad28-c531e3a1d9ac": {
                    "number_hosts": 1
                }
            }
        }
    
       An API specification of the metadata can be found `here`_.

    2. Report Slices. Report slices section of the data contains the general information about servers and hosts 
       generated during a scan. The report is separated into slices where the data is too large. Following is an example 
       of how each report slice should be formatted: ::

        {
            "report_slice_id": "2dd60c11-ee5b-4ddc-8b75-d8d34de86a34",
            "hosts": [
                {
                    "display_name": "7cent.cfoo.example.com",
                    "fqdn": "7cent.cfoo.example.com",
                    "facts": [
                        {
                            "namespace": "qpc",
                            "facts": {
                                "ip_addresses": [
                                    "192.168.121.50"
                                ],
                                "mac_addresses": [
                                    "52:54:00:ad:c3:c9"
                                ],
                                "subscription_manager_id": "f14ea675-7292-4bcd-9bc7-62197dd98e1b",
                                "name": "7cent.cfoo.example.com",
                                "os_release": "RHEL Server 7.3",
                                "os_version": 7.3,
                                "infrastructure_type": "virtualized",
                                "cpu_count": 2,
                                "architecture": "x86_64",
                                "is_redhat": True,
                                "cpu_socket_count": 2,
                                "cpu_core_count": 2
                            },
                            "rh_product_certs": [],
                            "rh_products_installed": [
                                "RHEL"
                            ]
                        }
                    ],
                    "system_profile": {
                        "infrastructure_type": "virtualized",
                        "architecture": "x86_64",
                        "os_release": "RHEL Server 7.3",
                        "os_kernel_version": "7.3",
                        "number_of_cpus": 2,
                        "number_of_sockets": 2,
                        "cores_per_socket": 1
                    }
                }
            ]
        }
       
       An API specification of the report slices can be found `here.`_

Tar.gz Format of the Data
^^^^^^^^^^^^^^^^^^^^^^^^^
Besides data being formatted in JSON, it can also be stored as a tar.gz file. In the tar.gz file, metadata and report slices 
are stored in separate .json files. The file that contains metadata information is named 'metadata.json', while the files containing 
report slices data are named with their uniquely generated 'report_slice_id' keys with .json extension. An example of such tar.gz file can be `found here`_.

.. _readthedocs: https://yupana.readthedocs.io/en/latest/
.. _here: docs/metadata.swagger.yml
.. _`here.`: docs/reportslices.swagger.yml
.. _`found here`: sample.tar.gz
.. |license| image:: https://img.shields.io/github/license/quipucords/yupana.svg
.. |Updates| image:: https://pyup.io/repos/github/quipucords/yupana/shield.svg
   :target: https://pyup.io/repos/github/quipucords/yupana/
.. |Python 3| image:: https://pyup.io/repos/github/quipucords/yupana/python-3-shield.svg
.. _`Install APIDoc`: http://apidocjs.com/#install
.. _`Working with Openshift`: https://yupana.readthedocs.io/en/latest/openshift.html
.. _tutorial: https://www.postgresql.org/docs/10/static/tutorial-start.html
