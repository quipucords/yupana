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
from aiokafka import AIOKafkaConsumer
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
    """
    file_data = {}
    try:
        tar = tarfile.open(fileobj=BytesIO(file_like_obj), mode='r:gz')
        files_check = tar.getmembers()
        if len(files_check) > 1:
            LOG.error('There are multiple files within the uploaded folder.')
        file = files_check[1]
        tarfile_obj = tar.extractfile(file)
        file_data = tarfile_obj.read().decode('utf-8')
        if '.json' in file.name:
            try:
                file_data = json.loads(file_data)
            except ValueError:
                LOG.error('File does not contain valid JSON')
        return file_data
    except Exception as err:
        print('Error: %s' % str(err))
        LOG.error('The file could not be read due to the following error: ', str(err))
        return file_data


def validate_contents(url):
    """
    Extract the file response from the url and validate the contents.

    :param url: The url where the file is located.
    :returns None
    """
    try:
        download_response = requests.get(url)
        contents = extract_tar_gz(download_response.content)
        is_valid = verify_report_details(contents)
        return is_valid

    except requests.exceptions.HTTPError as err:
        raise KafkaMsgHandlerError('Unable to download file. Error: ', str(err))


def verify_report_details(report_contents):
    """
    Verify that the report contents are a valid deployments report.

    Args:
        report_contents (dict): dictionary with report details
    Returns:
        boolean regarding report validity
    """
    DEPLOYMENTS_REPORT_TYPE = 'deployments'
    SYSTEM_FINGERPRINTS_KEY = 'system_fingerprints'
    valid = True

    # validate report id
    report_id = report_contents.get('report_id', None)
    if not report_id:
        # terminate if there is not a report id
        print('Not a valid QPC report')
        valid = False

    # validate version type
    report_version = report_contents.get('report_version', None)
    if not report_version:
        # terminate if there is not a report version
        print('Not a valid QPC report.')
        valid = False

    # validate report type
    report_type = report_contents.get('report_type', '')
    if report_type != DEPLOYMENTS_REPORT_TYPE:
        # terminate if different from deployments type
        print('Not a valid QPC report')
        valid = False

    # validate system fingerprints
    fingerprints = report_contents.get(SYSTEM_FINGERPRINTS_KEY, None)
    if not fingerprints:
        valid = False

    return valid


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
    Args:
        None
    Returns:
        None
    """
    if msg.topic == AVAILABLE_TOPIC:

        value = json.loads(msg.value.decode('utf-8'))
        service = value.get('service')

        if service == 'available':
            value = json.loads(msg.value.decode('utf-8'))
            message = 'The following message was placed on the "%s" topic: %s' % (QPC_TOPIC, msg)
            LOG.info(message)
            print(message)
            try:
                valid = validate_contents(value['url'])
                print(valid)
                return valid
            except KafkaMsgHandlerError as error:
                LOG.error('Unable to extract payload. Error: %s', str(error))

        # Decide if we want to keep track of confirmed messages.
        # If so we will have to store the hash for qpc topic msg and
        # look for them on a list here to get the validated url.
        LOG.info('File available: %s', value['url'])
    else:
        LOG.error('Unexpected Message')
    return None


async def process_messages():  # pragma: no cover
    """
    Process asyncio MSG_PENDING_QUEUE and send validation status.

    Args:
        None
    Returns:
        None
    """
    while True:
        msg = await MSG_PENDING_QUEUE.get()
        valid = handle_message(msg)
        if valid:
            print('The report is valid!')
        else:
            print('The report is not valid!')


async def listen_for_messages(consumer):  # pragma: no cover
    """
    Listen for messages on the available and qpc topics.

    Once a message from one of these topics arrives, we add
    them to the MSG_PENDING_QUEUE.
    Args:
        None
    Returns:
        None
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

    Args:
        None
    Returns:
        None
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

    Args:
        None
    Returns:
        None
    """
    event_loop_thread = threading.Thread(target=asyncio_worker_thread, args=(EVENT_LOOP,))
    event_loop_thread.daemon = True
    event_loop_thread.start()
