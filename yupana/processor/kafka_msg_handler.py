#
# Copyright 2018 Red Hat, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
"""Kafka message handler."""

import asyncio
import base64
import json
import logging
import tarfile
import threading
from io import BytesIO

import requests
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from kafka.errors import ConnectionError as KafkaConnectionError

from config.settings.base import (INSIGHTS_HOST_INVENTORY_URL,
                                  INSIGHTS_KAFKA_ADDRESS)

LOG = logging.getLogger(__name__)
EVENT_LOOP = asyncio.get_event_loop()
MSG_PENDING_QUEUE = asyncio.Queue()
QPC_TOPIC = 'platform.upload.qpc'
AVAILABLE_TOPIC = 'platform.upload.available'
VALIDATION_TOPIC = 'platform.upload.validation'
SUCCESS_CONFIRM_STATUS = 'success'
FAILURE_CONFIRM_STATUS = 'failure'


class KafkaMsgHandlerError(Exception):
    """Kafka msg handler error."""

    pass


def extract_tar_gz(file_like_obj):
    """Retrieve the contents of a tar.gz file like object.

    :param file_like_obj: A hexstring or BytesIO tarball saved in memory
    with gzip encryption.
    :returns file_data or False: Dictionary containing contents of json file or
        False
    """
    try:
        tar = tarfile.open(fileobj=BytesIO(file_like_obj), mode='r:gz')
        files_check = tar.getmembers()
        json_files = []
        for file in files_check:
            if '.json' in file.name:
                json_files.append(file)
        if len(json_files) > 1:
            LOG.error('Cannot process multiple files.')
            return False
        if json_files:
            file = json_files[0]
            tarfile_obj = tar.extractfile(file)
            file_data = tarfile_obj.read().decode('utf-8')
            try:
                file_data = json.loads(file_data)
            except ValueError:
                LOG.error('Uploaded file does not contain valid JSON.')
                return False
        else:
            LOG.error('Uploaded file does not contain a JSON file.')
            return False
        return file_data
    except Exception as err:
        LOG.error('Uploaded file could not be read due to the following error: ', str(err))
        return False


def download_response_content(msg_value):
    """
    Extract the response content from the url.

    :param msg_value: the value of the kakfa message
    :returns content: The content of the tar.gz
    """
    try:
        download_response = requests.get(msg_value['url'])
        contents = extract_tar_gz(download_response.content)
        return contents
    except requests.exceptions.HTTPError as err:
        raise KafkaMsgHandlerError('Unable to download file. Error: ', str(err))


def verify_report_fingerprints(fingerprints, report_platform_id):
    """Verify that report fingerprints contain canonical facts.

    :param fingerprints: list of fingerprints
    :param report_platform_id: str containing report_platform_id
    :returns valid_fingerprints (list), invalid_fingerprints(list)
    """
    canonical_facts = ['insights_client_id', 'bios_uuid', 'ip_addresses', 'mac_addresses',
                       'vm_uuid', 'etc_machine_id', 'subscription_manager_id']
    valid_fingerprints = []
    invalid_fingerprints = []
    for fingerprint in fingerprints:
        found_facts = False
        for fact in canonical_facts:
            if fingerprint.get(fact):
                found_facts = True
                break
        if found_facts:
            valid_fingerprints.append(fingerprint)
        else:
            fingerprint.pop('metadata', None)
            invalid_fingerprints.append(fingerprint)
    fp_msg = 'Report with report_platform_id "%s" contains %s/%s valid fingerprints.'
    LOG.info(fp_msg % (report_platform_id, len(valid_fingerprints), len(fingerprints)))
    if invalid_fingerprints:
        message = 'report_platform_id: "%s"| These fingerprints were removed because they had no canonical facts: %s'
        LOG.debug(message % (report_platform_id, invalid_fingerprints))

    # Invalid fingerprints is for future use.
    return valid_fingerprints, invalid_fingerprints


def verify_report_details(report_contents):
    """
    Verify that the report contents are a valid deployments report.

    :param report_contents: dictionary with report details
    :returns success or failure report validity
    """
    missing_message = 'Report is missing required field "%s".'
    required_keys = ['report_platform_id',
                     'report_id',
                     'report_version',
                     'report_type',
                     'system_fingerprints']
    for key in required_keys:
        check = report_contents.get(key)
        if not check:
            LOG.error(missing_message % key)
            return (FAILURE_CONFIRM_STATUS, [], [])

    if report_contents['report_type'] != 'deployments':
        LOG.error('Report has an invalid value for required field "%s".'
                  % 'report_type')
        return (FAILURE_CONFIRM_STATUS, [], [])

    valid_fingerprints, invalid_fingerprints = \
        verify_report_fingerprints(report_contents['system_fingerprints'],
                                   report_contents['report_platform_id'])
    if not valid_fingerprints:
        err_msg = 'Report "%s" contained no valid fingerprints.'
        LOG.error(err_msg % report_contents['report_platform_id'])
        return (FAILURE_CONFIRM_STATUS, [], [])
    else:
        return (SUCCESS_CONFIRM_STATUS, valid_fingerprints, invalid_fingerprints)


def upload_to_host_inventory(account_number, fingerprints, report_platform_id):
    """
    Verify that the report contents are a valid deployments report.

    :param account_number: <str> of the User's account number.
    :param fingerprints: a list of dictionaries that have been validated.
    :param report_platform_id: <str> of the report platform id
    :returns True or False for if fingerprints is uploaded to inventory.
    """
    if account_number in [None, 'null', '']:
        LOG.error('An account number is required to upload to host inventory.')
        return False
    identity_string = '{"identity": {"account_number": "%s"}}' % str(account_number)
    bytes_string = identity_string.encode()
    x_rh_identity_value = base64.b64encode(bytes_string).decode()
    identity_header = {'x-rh-identity': x_rh_identity_value,
                       'Content-Type': 'application/json'}
    failed_fingerprints = []
    for fingerprint in fingerprints:
        body = dict()
        facts = dict()
        body['account'] = account_number
        body['bios_uuid'] = fingerprint.get('bios_uuid')
        body['display_name'] = fingerprint.get('name')
        body['ip_addresses'] = fingerprint.get('ip_addresses')
        body['mac_addresses'] = fingerprint.get('mac_addresses')
        body['insights_id'] = fingerprint.get('insights_client_id')
        body['rhel_machine_id'] = fingerprint.get('etc_machine_id')
        body['subscription_manager_id'] = fingerprint.get('subscription_manager_id')
        body['fqdn'] = fingerprint.get('name')
        facts['namespace'] = 'qpc'
        facts['facts'] = fingerprint
        body['facts'] = [facts]
        try:
            request = requests.post(INSIGHTS_HOST_INVENTORY_URL,
                                    data=json.dumps(body),
                                    headers=identity_header)
        except requests.exceptions.RequestException as err:
            err_msg = 'Posting to (%s) returned error: %s'
            LOG.debug(err_msg % (INSIGHTS_HOST_INVENTORY_URL, err))
        if request.status_code not in [200, 201]:
            failed_fingerprints.append(fingerprint)
    successful = len(fingerprints) - len(failed_fingerprints)
    upload_msg = '%s/%s fingerprints were uploaded to the host inventory system.'
    if successful != len(fingerprints):
        LOG.warning(upload_msg % (successful, len(fingerprints)))
    else:
        LOG.info(upload_msg % (successful, len(fingerprints)))
    if failed_fingerprints:
        message = 'report_platform_id: "%s"| The following fingerprints failed to upload to host inventory system: %s'
        LOG.debug(message % (report_platform_id, failed_fingerprints))
    return True


async def send_confirmation(file_hash, status):  # pragma: no cover
    """
    Send kafka validation message to Insights Upload service.

    When a new file lands for topic 'qpc' we must validate it
    so that it will be made permanently available to other
    apps listening on the 'available' topic.
    :param: file_hash (String): Hash for file being confirmed.
    :param: status (String): Either 'success' or 'failure'
    :returns None
    """
    producer = AIOKafkaProducer(
        loop=EVENT_LOOP, bootstrap_servers=INSIGHTS_KAFKA_ADDRESS
    )
    try:
        await producer.start()
    except (KafkaConnectionError, TimeoutError):
        await producer.stop()
        raise KafkaMsgHandlerError('Unable to connect to kafka server.  Closing producer.')
    try:
        validation = {
            'hash': file_hash,
            'validation': status
        }
        msg = bytes(json.dumps(validation), 'utf-8')
        await producer.send_and_wait(VALIDATION_TOPIC, msg)
    finally:
        await producer.stop()


async def process_messages():  # pragma: no cover
    """
    Process asyncio MSG_PENDING_QUEUE and send validation status.

    :param None
    :returns None
    """
    while True:
        msg = await MSG_PENDING_QUEUE.get()
        if msg.topic == QPC_TOPIC:
            message = 'The following message was placed on the "%s" topic: %s'
            LOG.info(message % (QPC_TOPIC, msg))
            msg_value = json.loads(msg.value.decode('utf-8'))
            content = download_response_content(msg_value)
            if content:
                results_tuple = verify_report_details(content)
                status = results_tuple[0]
                valid_prints = results_tuple[1]
            else:
                status = FAILURE_CONFIRM_STATUS
            LOG.info('Sending confirmation "%s", for msg with hash "%s".'
                     % (status, msg_value['hash']))
            await send_confirmation(msg_value['hash'], status)
            if status == SUCCESS_CONFIRM_STATUS:
                upload_to_host_inventory(msg_value['rh_account'],
                                         valid_prints,
                                         content['report_platform_id'])
        else:
            LOG.info('Unexpected Message')


async def listen_for_messages(consumer):  # pragma: no cover
    """
    Listen for messages on the available and qpc topics.

    Once a message from one of these topics arrives, we add
    them to the MSG_PENDING_QUEUE.
    :param consumer : Kafka consumer
    :returns None
    """
    try:
        await consumer.start()
    except KafkaConnectionError:
        await consumer.stop()
        raise KafkaMsgHandlerError('Unable to connect to kafka server.  Closing consumer.')

    LOG.info('Listener started.  Waiting for messages...')
    try:
        # Consume messages
        async for msg in consumer:
            await MSG_PENDING_QUEUE.put(msg)
    finally:
        # Will leave consumer group; perform autocommit if enabled.
        await consumer.stop()


def asyncio_worker_thread(loop):  # pragma: no cover
    """
    Worker thread function to run the asyncio event loop.

    :param None
    :returns None
    """
    consumer = AIOKafkaConsumer(
        AVAILABLE_TOPIC, QPC_TOPIC,
        loop=EVENT_LOOP, bootstrap_servers=INSIGHTS_KAFKA_ADDRESS,
        group_id='qpc-group'
    )

    loop.create_task(process_messages())

    try:
        loop.run_until_complete(listen_for_messages(consumer))
    except KafkaMsgHandlerError as err:
        LOG.info('Stopping kafka worker thread.  Error: %s', str(err))


def initialize_kafka_handler():  # pragma: no cover
    """
    Create asyncio tasks and daemon thread to run event loop.

    :param None
    :returns None
    """
    event_loop_thread = threading.Thread(target=asyncio_worker_thread, args=(EVENT_LOOP,))
    event_loop_thread.daemon = True
    event_loop_thread.start()
