[![GitHub license](https://img.shields.io/github/license/quipucords/yupana.svg)](https://github.com/quipucords/yupana/blob/master/LICENSE)
[![Code Coverage](https://codecov.io/gh/quipucords/yupana/branch/master/graph/badge.svg)](https://codecov.io/gh/quipucords/yupana)
[![Documentation Status](https://readthedocs.org/projects/yupana/badge/)](https://yupana.readthedocs.io/en/latest/)
[![Updates](https://pyup.io/repos/github/quipucords/yupana/shield.svg)](https://pyup.io/repos/github/quipucords/yupana/)
[![Python 3](https://pyup.io/repos/github/quipucords/yupana/python-3-shield.svg)](https://pyup.io/repos/github/quipucords/yupana/)

# Overview
- [Getting started](#intro)
- [Development](#development)
- [Formatting Data for Yupana (without QPC)](#formatting_data)
- [Sending Data to Insights Upload service for Yupana (without QPC)](#sending_data)
- [Advanced Topics](#advanced)

Full documentation is available through [readthedocs](https://yupana.readthedocs.io/en/latest/).

# <a name="intro"></a> Getting Started

This is a Python project developed using Python 3.6. Make sure you have at least this version installed.

# <a name="development"></a> Development

## Setup

### Obtain source for local projects
To get started developing against Yupana first clone a local copy of the git repository.
```
git clone https://github.com/quipucords/yupana
git clone https://github.com/RedHatInsights/insights-upload.git
git clone https://github.com/RedHatInsights/insights-host-inventory.git
```

### Configure environment variables
This project is developed using the Django web framework. Many configuration settings can be read in from a `.env` file. An example file `.env.dev.example` is provided in the repository. To use the defaults simply:
```
cp .env.dev.example .env
```

Modify as you see fit.

### Update /etc/hosts
The `/etc/hosts` file must be updated for Kafka and Minio.  Open your `/etc/hosts` file and add these lines to the end.

```
127.0.0.1       kafka
127.0.0.1       minio
```

### Using pipenv
A Pipfile is provided. Pipenv is recommended for combining virtual environment (virtualenv) and dependency management (pip). To install pipenv, use pip :

```
pip3 install pipenv
```

Then project dependencies and a virtual environment can be created using:
```
pipenv install --dev
```
### Bringing up yupana with all services

To locally run the file upload service, yupana, and host inventory service run the following command:
```
make local-dev-up
```

### Sending data to local yupana
To send the sample data, run the following commands:
1. Prepare the sample for sending
    ```
    make sample-data
    ```

2. Locate the temp file name.  You will see a message like the following:
    ```
    The updated report was written to temp/sample_data_ready_1561410754.tar.gz
    ```
3. Send the temp file to your local yupana.  Copy the name of this file to the upload command as shown below:
    ```
    make local-upload-data file=temp/sample_data_ready_1561410754.tar.gz
    ```
4. Watch the kafka consumer for a message to arrive.  You will see something like this in the consumer iTerm.
    ```
    {"account": "12345", "rh_account": "12345", "principal": "54321", "request_id": "52df9f748eabcfea", "payload_id": "52df9f748eabcfea", "size": 1132, "service": "qpc", "category": "tar", "b64_identity": "eyJpZGVudGl0eSI6IHsiYWNjb3VudF9udW1iZXIiOiAiMTIzNDUiLCAiaW50ZXJuYWwiOiB7Im9yZ19pZCI6ICI1NDMyMSJ9fX0=", "url": "http://minio:9000/insights-upload-perm-test/52df9f748eabcfea?AWSAccessKeyId=BQA2GEXO711FVBVXDWKM&Signature=WEgFnnKzUTsSJsQ5ouiq9HZG5pI%3D&Expires=1561586445"}

5. Look at the yupana logs to follow the report processing to completion.
    ```

### Bringing down yupana and all services
To bring down all services run:
```
make local-dev-down
```

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

If you would like to run a single test you can do this.
```
tox -e py36 -- processor.tests_report_processor.ReportProcessorTests.test_archiving_report
```
**Note:** You can specify any module or class to run all tests in the class or module.

To lint the code base:
```
tox -e lint
```


# <a name="formatting_data"></a> Formatting Data for Yupana (without QPC)
Below is a description of how to create data formatted for the yupana service.

## Yupana tar.gz File Format Overview
Yupana retrieves data from the Insights platform file upload service.  Yupana requires a specially formatted tar.gz file.  Files that do not conform to the required format will be marked as invalid and no processing will occur.  The tar.gz file must contain a metadata JSON file and one or more report slice JSON files. The file that contains metadata information is named `metadata.json`, while the files containing host data are named with their uniquely generated UUID4 `report_slice_id` followed by the .json extension. You can download [sample.tar.gz](https://github.com/quipucords/yupana/raw/master/sample.tar.gz) to view an example.

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
Yupana has a sample `tar.gz` file to showcase how to upload data to Insights. To prepare the sample data for upload, simply run:
```
make sample-data
```

This command will use the `sample.tar.gz` file in the Yupana repository, change the UUIDs within the metadata & each report slice, and save it as a new `tar.gz` file.  Newly generated `tar.gz` files are located in the `temp/` directory.

## Preparing Custom Data for Upload
In addition to preparing a sample `tar.gz` file, you also have the option to prepare your own data for uploading to Insights. To prepare your custom data for upload, simply run:
```
make custom-data file=<path/to/your-data.tar.gz>
```

Replace the `<path/to/your-data.tar.gz>` with either the absolute or relative path to the `tar.gz` file holding your data. This command will copy your data files into the `temp/` directory, change the UUIDs and place the files into a new `tar.gz` file inside the `temp/` directory.

## Uploading Data
After preparing the data with new UUIDs through either of the above steps, you can upload it to Insights. Additionally, you must export the following required information as environment variables or add them to your `.env` file:
```
RH_ACCOUNT_NUMBER=<your-account-number>
RH_ORG_ID=<your-org-id>
FILE_UPLOAD_URL=<file-upload-url>
RH_USERNAME=<your-username>
RH_PASSWORD=<your-password>
```

To upload the data, run:
```
make upload-data file=<path/to/your-data.tar.gz>
```

You need to replace `<path/to/your-data.tar.gz>` with either the absolute or relative path to the `tar.gz` file that you want to upload to Insights.

After running this command if you see `HTTP 202` like the following lines in your output logs, it means your file upload to Insights was successful:
```
* Connection state changed (MAX_CONCURRENT_STREAMS updated)!
< HTTP/2 202
```

# <a name="advanced"></a> Advanced Topics
## Database

PostgreSQL is used as the database backend for Yupana. If modifications were made to the .env file the docker-compose file will need to be modified to ensure matching database credentials. Several commands are available for interacting with the database.

Assuming the default .env file values are used, to access the database directly using psql run:
```
psql postgres -U postgres -h localhost -p 15432
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