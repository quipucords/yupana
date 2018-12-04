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
import json
import logging
import tarfile
import threading
from io import BytesIO

import requests
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from kafka.errors import ConnectionError as KafkaConnectionError

from config.settings.base import INSIGHTS_KAFKA_ADDRESS

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
    :returns file_data or status: Dictionary containing contents of json file or
        FAILURE status
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
            return FAILURE_CONFIRM_STATUS
        if json_files:
            file = json_files[0]
            tarfile_obj = tar.extractfile(file)
            file_data = tarfile_obj.read().decode('utf-8')
            try:
                file_data = json.loads(file_data)
            except ValueError:
                LOG.error('Uploaded file does not contain valid JSON.')
                return FAILURE_CONFIRM_STATUS
        else:
            LOG.error('Uploaded file does not contain a JSON file.')
            return FAILURE_CONFIRM_STATUS
        return file_data
    except Exception as err:
        LOG.error('Uploaded file could not be read due to the following error: ', str(err))
        return FAILURE_CONFIRM_STATUS


def download_and_validate_contents(url):
    """
    Extract the file response from the url and validate the contents.

    :param url: String containing the url where the file is located.
    :returns None
    """
    try:
        download_response = requests.get(url)
        contents = extract_tar_gz(download_response.content)
        if contents == FAILURE_CONFIRM_STATUS:
            return contents
        status = verify_report_details(contents)
        if status == SUCCESS_CONFIRM_STATUS:
            LOG.info('Uploaded file contained a valid QPC report with report_platform_id "%s".',
                     contents.get('report_platform_id'))
        else:
            LOG.error('Uploaded file does not contain a valid QPC report.')
        return status

    except requests.exceptions.HTTPError as err:
        raise KafkaMsgHandlerError('Unable to download file. Error: ', str(err))


def verify_report_details(report_contents):
    """
    Verify that the report contents are a valid deployments report.

    :param report_contents: dictionary with report details
    :returns success or failure report validity
    """
    status = SUCCESS_CONFIRM_STATUS
    message = 'Report is missing required field "%s".'

    # validate report_platform_id
    report_platform_id = report_contents.get('report_platform_id')
    if not report_platform_id:
        LOG.error(message % 'report_platform_id')
        status = FAILURE_CONFIRM_STATUS

    # validate report id
    report_id = report_contents.get('report_id')
    if not report_id:
        LOG.error(message % 'report_id')
        status = FAILURE_CONFIRM_STATUS

    # validate version type
    report_version = report_contents.get('report_version')
    if not report_version:
        LOG.error(message % 'report_version')
        status = FAILURE_CONFIRM_STATUS

    # validate report type
    report_type = report_contents.get('report_type', '')
    if report_type != 'deployments':
        LOG.error('Report has a missing or invalid value for required field "%s".'
                  % 'report_type')
        status = FAILURE_CONFIRM_STATUS

    # validate system fingerprints
    fingerprints = report_contents.get('system_fingerprints')
    if not fingerprints:
        LOG.error(message % 'system_fingerprints')
        status = FAILURE_CONFIRM_STATUS

    if fingerprints and report_platform_id:
        verified_fingerprints = verify_report_fingerprints(fingerprints, report_platform_id)
        if verified_fingerprints:
            upload_to_host_inventory(verified_fingerprints)
        else:
            LOG.error('Report "%s" contained no valid fingerprints.' % report_platform_id)
            status = FAILURE_CONFIRM_STATUS

    return status


def upload_to_host_inventory(fingerprints):
    """Scaffolding for host inventory upload."""
    pass


def verify_report_fingerprints(fingerprints, report_platform_id):
    """Verify that report fingerprints contain canonical facts."""
    canonical_facts = ['insights_client_id', 'bios_uuid', 'ip_addresses', 'mac_addresses',
                       'vm_uuid', 'etc_machine_id', 'subscription_manager_id']
    message = 'report_platform_id: "%s"| Removing invalid fingerprint. No canonical facts: %s.'
    verified_fingerprints = []
    for fingerprint in fingerprints:
        found_facts = False
        for fact in canonical_facts:
            if fingerprint.get(fact):
                found_facts = True
                break
        if found_facts:
            verified_fingerprints.append(fingerprint)
        else:
            LOG.warning(message % (report_platform_id, fingerprint.pop('metadata', None)))
    return verified_fingerprints


def handle_message(msg):
    """
    Handle messages from pending queue with QPC_TOPIC & AVALIABLE_TOPIC.

    The QPC report payload will land on the qpc topic.
    These messages will be extracted into the local report
    directory structure.  Once the file has been verified
    (successfully extracted) we will report the status to
    the Insights Upload Service so the file can be made available
    to other apps on the service.
    Messages on the available topic are messages that have
    been verified by an app on the Insights upload service.
    For now we are just logging the URL for demonstration purposes.
    In the future if we want to maintain a URL to our report files
    in the upload service we could look for hashes for files that
    we have previously validated on the qpc topic.
    :param None
    :returns None
    """
    if msg.topic == QPC_TOPIC:

        value = json.loads(msg.value.decode('utf-8'))
        message = 'The following message was placed on the "%s" topic: %s' % (QPC_TOPIC, msg)
        LOG.info(message)
        try:
            status = download_and_validate_contents(value['url'])
            return status
        except KafkaMsgHandlerError as error:
            LOG.error('Unable to extract payload. Error: %s', str(error))
            return FAILURE_CONFIRM_STATUS

    elif msg.topic == AVAILABLE_TOPIC:
        value = json.loads(msg.value.decode('utf-8'))
        service = value.get('service')
        # Decide if we want to keep track of confirmed messages.
        # If so we will have to store the hash for qpc topic msg and
        # look for them on a list here to get the validated url.
        LOG.info('File available: %s for service %s' % (value['url'], service))
    else:
        LOG.error('Unexpected Message')
    return None


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
        status = handle_message(msg)
        if status:
            value = json.loads(msg.value.decode('utf-8'))
            LOG.info('Sending confirmation "%s", for msg with hash "%s".'
                     % (status, value['hash']))
            await send_confirmation(value['hash'], status)


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
