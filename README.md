[![GitHub license](https://img.shields.io/github/license/quipucords/yupana.svg)](https://github.com/quipucords/yupana/blob/master/LICENSE)
[![Documentation Status](https://readthedocs.org/projects/yupana/badge/)](https://yupana.readthedocs.io/en/latest/)
[![Updates](https://pyup.io/repos/github/quipucords/yupana/shield.svg)](https://pyup.io/repos/github/quipucords/yupana/)
[![Python 3](https://pyup.io/repos/github/quipucords/yupana/python-3-shield.svg)](https://pyup.io/repos/github/quipucords/yupana/)

# Overview
- [Getting started](#intro)
- [Development](#development)
- [Formatting Data for Yupana (without QPC)](#formatting_data)
- [Sending Data to Insights Upload service for Yupana (without QPC)](#sending_data)

Full documentation is available through [readthedocs](https://yupana.readthedocs.io/en/latest/).

# <a name="intro"></a> Getting Started

This is a Python project developed using Python 3.6. Make sure you have at least this version installed.

# <a name="development"></a> Development

To get started developing against Yupana first clone a local copy of the git repository.
```
git clone https://github.com/quipucords/yupana
```

Developing inside a virtual environment is recommended. A Pipfile is provided. Pipenv is recommended for combining virtual environment (virtualenv) and dependency management (pip). To install pipenv, use pip :

```
pip3 install pipenv
```

Then project dependencies and a virtual environment can be created using:
```
pipenv install --dev
```

To activate the virtual environment run:
```
pipenv shell
```
## Configuration

This project is developed using the Django web framework. Many configuration settings can be read in from a `.env` file. An example file `.env.example` is provided in the repository. To use the defaults simply:
```
cp .env.example .env
```

Modify as you see fit.

## Database

PostgreSQL is used as the database backend for Yupana. A docker-compose file is provided for creating a local database container. If modifications were made to the .env file the docker-compose file will need to be modified to ensure matching database credentials. Several commands are available for interacting with the database.

```
# This will launch a Postgres container
make start-db

# This will run Django's migrations against the database
make server-migrate
```
This will stop and remove a currently running database and run the above commands. If this command fails, try running each command it combines separately, using `docker ps` in between to track the existence of the db.  You can reinit with:
```
make reinit-db
```

Assuming the default .env file values are used, to access the database directly using psql run:
```
psql postgres -U postgres -h localhost -p 15432
```

There is a known limitation with docker-compose and Linux environments with SELinux enabled. You may see the following error during the postgres container deployment:
```
"mkdir: cannot create directory '/var/lib/pgsql/data/userdata': Permission denied" can be resolved by granting ./pg_data ownership permissions to uid:26 (postgres user in centos/postgresql-96-centos7)
```

If this error is encountered, the following command can be used to grant `pg_data` ownership permissions to uid:26 as the error suggests:
```
setfacl -m u:26:-wx ./pg_data/
```

If a docker container running Postgres is not feasible, it is possible to run Postgres locally as documented in the [Postgres tutorial](https://www.postgresql.org/docs/10/static/tutorial-start.html). The default port for local Postgres installations is ``5432``. Make sure to modify the `.env` file accordingly. To initialize the database run:
```
make server-migrate
```

## Server

To run a local dev Django server you can use:
```
make server-init
make serve
```
## Run Server with gunicorn

To run a local gunicorn server with yupana do the following:
```
make server-init
gunicorn config.wsgi -c ./yupana/config/gunicorn.py --chdir=./yupana/
```

## Preferred Environment

Please refer to [Working with Openshift](https://yupana.readthedocs.io/en/latest/openshift.html).


## API Documentation Generation

To generate and host the API documentation locally you need to [Install APIDoc](http://apidocjs.com/#install).

Generate the project API documentation by running the following command:
```
make gen-apidoc
```

In order to host the docs locally you need to collect the static files:
```
make server-static
```

Now start the server as described above and point your browser to
http://127.0.0.1:8001/apidoc/index.html

## Testing and Linting

Yupana uses tox to standardize the environment used when running tests. Essentially, tox manages its own virtual environment and a copy of required dependencies to run tests. To ensure a clean tox environment run:
```
tox -r
```

This will rebuild the tox virtual env and then run all tests.

To run unit tests specifically:
```
tox -e py36
```

To lint the code base:
```
tox -e lint
```

# <a name="formatting_data"></a> Formatting Data for Yupana (without QPC)
Below is a description of how to create data formatted for the yupana service.

## Yupana tar.gz File Format Overview
Yupana retrieves data from the Insights platform file upload service.  Yupana requires a specially formatted tar.gz file.  Files that do not conform to the required format will be marked as invalid and no processing will occur.  The tar.gz file contains a metadata JSON file and one or more report slice JSON files. The file that contains metadata information is named `metadata.json`, while the files containing host data are named with their uniquely generated UUID4 `report_slice_id` followed by the .json extension. You can download [sample.tar.gz](https://github.com/quipucords/yupana/raw/master/sample.tar.gz) to view an example.

## Yupana Meta-data JSON Format
Metadata should include information about the sender of the data, Host Inventory API version, and the report slices included in the tar.gz file. Below is a sample metadata section for a report with 2 slices:
```
{
    "report_id": "05f373dd-e20e-4866-b2a4-9b523acfeb6d",
    "host_inventory_api_version": "1.0",
    "source": "satellite",
    "source_metadata": {
        "any_satellite_info_you_want": "some stuff that will not be validated but will be logged"
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
```

An API specification of the metadata can be found in [metadata.yml](https://github.com/quipucords/yupana/blob/master/docs/metadata.yml).

## Yupana Report Slice JSON Format
Report slices are a slice of the host inventory data for a given report. A slice limits the number of hosts to 10K.  Slices with more than 10K hosts will be discarded as a validation error. Below is a sample report slice:
```
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
                    "namespace": "satellite",
                    "facts": {
                        "rh_product_certs": [69],
                        "rh_products_installed": [
                            "RHEL"
                        ]
                    }
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
```

An API specification of the report slices can be found in [report_slices.yml](https://github.com/quipucords/yupana/blob/master/docs/report_slices.yml). The host based inventory api specification includes a mandatory `account` field. Yupana will extract the `account` number from the kafka message it receives from the Insights platform file upload service and populate the `account` field of each host.

# <a name="sending_data"></a> Sending Data to Insights Upload service for Yupana (without QPC)
Data being uploaded to Insights must be in `tar.gz` format containing the `.json` files with the given JSON structure above. It is important to note that Yupana processes & tracks reports based on their UUIDS, which means that data with a specific UUID cannot be uploaded more than once, or else the second upload will be archived and not processed. Therefore, before every upload we need to generate a new UUID and replace the current one with it if we want to upload the same data more than once. Use the following instructions to prepare and upload a sample or custom report.

## Preparing Yupana Sample Data for Upload
Yupana has a sample `tar.gz` file to showcase file upload to Insights. To prepare the data for upload, simply run:
```
make sample-data
```

This command will use the `sample.tar.gz` file in the Yupana repository, change UUIDs of the report, and save it as a new `tar.gz` file.  Newly generated `tar.gz` files are found in `temp/` directory.

## Preparing Custom Data for Upload
Besides sending a sample `tar.gz` file, you also have the option to send your own reports data to Insights. To prepare the data for upload,
simply run:
```
make custom-data data_file=<path/to/your-data.tar.gz>
```

Replace the `<path/to/your-data.tar.gz-dir>` with your reports data file path. You can either provide absolute path or relative path to the Yupana project. Your data should be in `tar.gz` format. This command will copy your data files into temp folder, change the UUIDs and place the files into a new `tar.gz` file inside `temp/` folder.

## Uploading Data
After generating the data with new UUIDs through either of the above steps, now you can upload it to Insights. To upload the data, run: ::
```
make upload-data file=<filename>
```

You need to replace `<filename>` with the path to `tar.gz` file you want to upload to Insights (we generated this in previous steps). Besides, there are other variables
such as `RH_ACCOUNT_NUMBER`, `RH_ORG_ID`, `FILE_UPLOAD_URL`, `RH_USERNAME`,
AND `RH_PASSWORD` that need to be exported as environment variables in the `.env` file with necessary values, since we also use them to validate to the upload host.

After running this command if you see `HTTP 202` like the following lines in your output logs, it means your file upload to Insights was successful:
```
* Connection state changed (MAX_CONCURRENT_STREAMS updated)!
< HTTP/2 202
```