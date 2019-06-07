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


Formatting Data for Yupana (without QPC)
========================================

Yupana tar.gz File Format Overview
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Yupana retrieves data from the Insights platform file upload service.  Yupana requires a specially formatted tar.gz file.  Files that do not conform to the required format will be marked as invalid and no processing will occur.  The tar.gz file contains a metadata JSON file and one or more report slices JSON files. The file that contains metadata information is named 'metadata.json', while the files containing report slices data are named with their uniquely generated UUID4 'report_slice_id' keys with .json extension. An example of such tar.gz file can be `found here`_.

Yupana Meta-data JSON Format
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Metadata should include information about the sender of the data, Host Inventory API version, and the report slices included in the tar.gz file. Below is a sample metadata section for a report with 2 slices::

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

Yupana Report Slice JSON Format
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Report slices are a slice of the host inventory data for a given report. A slice limits the number of hosts to 10K.  Slices with more than 10K hosts will be discarded as a validation error. Below is a sample report slice::

    {
        "report_slice_id": "2dd60c11-ee5b-4ddc-8b75-d8d34de86a34",
        "hosts": [
            {
                "display_name": "dhcp181-3.gsslab.rdu2.redhat.com",
                "fqdn": "dhcp181-3.gsslab.rdu2.redhat.com",
                "bios_uuid": "848F1E42-51ED-8D58-9FA4-E0B433EEC7E3",
                "ip_addresses": [
                    "10.10.182.241"
                ],
                "mac_addresses": [
                    "00:50:56:9e:f7:d6"
                ],
                "subscription_manager_id": "848F1E42-51ED-8D58-9FA4-E0B433EEC7E3",
                "facts": [
                    {
                        "namespace": "qpc",
                        "facts": {
                            "bios_uuid": "848F1E42-51ED-8D58-9FA4-E0B433EEC7E3",
                            "ip_addresses": [
                                "10.10.182.241"
                            ],
                            "mac_addresses": [
                                "00:50:56:9e:f7:d6"
                            ],
                            "subscription_manager_id": "848F1E42-51ED-8D58-9FA4-E0B433EEC7E3",
                            "name": "dhcp181-3.gsslab.rdu2.redhat.com",
                            "os_release": "Red Hat Enterprise Linux Server release 6.9 (Santiago)",
                            "os_version": "6.9 (Santiago)",
                            "infrastructure_type": "virtualized",
                            "cpu_count": 1,
                            "architecture": "x86_64",
                            "is_redhat": true,
                            "redhat_certs": "69.pem",
                            "cpu_core_per_socket": 1,
                            "cpu_socket_count": 1,
                            "cpu_core_count": 1
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
                    "os_release": "Red Hat Enterprise Linux Server release 6.9 (Santiago)",
                    "os_kernel_version": "6.9 (Santiago)",
                    "number_of_cpus": 1,
                    "number_of_sockets": 1,
                    "cores_per_socket": 1
                }
            }
        ]
    }

An API specification of the report slices can be found `here.`_
The host based inventory api specification includes a mandatory ``account`` field.
Yupana will extract the ``account`` number from the kafka message it receives from the Insights platform
file upload service and populate the ``account`` field of each host.

.. _readthedocs: https://yupana.readthedocs.io/en/latest/
.. _here: https://github.com/quipucords/yupana/blob/master/docs/metadata.yml
.. _`here.`: https://github.com/quipucords/yupana/blob/master/docs/report_slices.yml
.. _`found here`: https://github.com/quipucords/yupana/raw/master/sample.tar.gz
.. |license| image:: https://img.shields.io/github/license/quipucords/yupana.svg
.. |Updates| image:: https://pyup.io/repos/github/quipucords/yupana/shield.svg
   :target: https://pyup.io/repos/github/quipucords/yupana/
.. |Python 3| image:: https://pyup.io/repos/github/quipucords/yupana/python-3-shield.svg
.. _`Install APIDoc`: http://apidocjs.com/#install
.. _`Working with Openshift`: https://yupana.readthedocs.io/en/latest/openshift.html
.. _tutorial: https://www.postgresql.org/docs/10/static/tutorial-start.html