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
from http import HTTPStatus
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
CANONICAL_FACTS = ['insights_client_id', 'bios_uuid', 'ip_addresses', 'mac_addresses',
                   'vm_uuid', 'etc_machine_id', 'subscription_manager_id']


class QPCReportException(Exception):
    """Use to report errors during qpc report processing."""

    pass


class KafkaMsgHandlerError(Exception):
    """Kafka msg handler error."""

    pass


def unpack_consumer_record(upload_service_message):
    """Retreive report URL from kafka.

    :param upload_service_message: the value of the kakfa message from file
        upload service.
    :returns: str containing the url to the qpc report's tar.gz file.
    """
    try:
        json_message = json.loads(upload_service_message.value.decode('utf-8'))
        LOG.info('Message received on %s topic for rh_account=%s',
                 upload_service_message.topic,
                 json_message.get('rh_account', 'None'))
        LOG.debug('Message: %s', upload_service_message)
        return json_message
    except ValueError:
        raise QPCReportException('Upload service kafka message does not contain valid JSON.')


def download_response_content(upload_service_message):
    """
    Download report.

    :param upload_service_message: the value of the kakfa message
    :returns content: The tar.gz binary content or None if there are errors.
    """
    try:
        report_url = upload_service_message.get('url', None)
        if not report_url:
            raise QPCReportException(
                'Invalid kafka message from file upload service.  Missing report url.  Message: %s',
                upload_service_message)

        download_response = requests.get(report_url)
        if download_response.status_code != HTTPStatus.OK:
            raise QPCReportException(
                'Could not download report file at URL %s.  Message: %s',
                report_url,
                upload_service_message)

        return download_response.content
    except requests.exceptions.HTTPError as err:
        raise QPCReportException(
            'Unexpected http error while requesting %s.  Message: %s, Error: %s',
            report_url,
            upload_service_message,
            err)


def extract_report_from_tar_gz(report_tar_gz):
    """Extract deployment report from tar.gz file.

    :param report_tar_gz: A hexstring or BytesIO tarball
        saved in memory with gzip compression.
    :returns: Deployment report as dict
    """
    try:
        tar = tarfile.open(fileobj=BytesIO(report_tar_gz), mode='r:gz')
        files_check = tar.getmembers()
        json_files = []
        for file in files_check:
            if '.json' in file.name:
                json_files.append(file)
        if len(json_files) > 1:
            raise QPCReportException('Report tar.gz cannot contain multiple files.')
        if json_files:
            file = json_files[0]
            tarfile_obj = tar.extractfile(file)
            report_json_str = tarfile_obj.read().decode('utf-8')
            try:
                deployment_report = json.loads(report_json_str)
                return deployment_report
            except ValueError as error:
                raise QPCReportException('Uploaded file does not contain valid JSON. Error: %s' % str(error))
        raise QPCReportException('Uploaded file does not contain a JSON file.')
    except Exception as err:
        raise QPCReportException('Uploaded file could not be read due to the following error: %s' % str(err))


def verify_report_details(deployments_report):
    """
    Verify that the report contents are a valid deployments report.

    :param deployments_report: dict with report
    :returns: tuple contain list of valid and invalid fingerprints
    """
    missing_message = 'Report is missing required field "%s".'
    required_keys = ['report_platform_id',
                     'report_id',
                     'report_version',
                     'report_type',
                     'system_fingerprints']
    for key in required_keys:
        check = deployments_report.get(key)
        if not check:
            raise QPCReportException(missing_message % key)

    if deployments_report['report_type'] != 'deployments':
        raise QPCReportException('Report has an invalid value for required field "%s".'
                                 % 'report_type')

    valid_fingerprints, invalid_fingerprints = \
        verify_report_fingerprints(deployments_report)
    if not valid_fingerprints:
        err_msg = 'Report "%s" contained no valid fingerprints.'
        raise QPCReportException(err_msg % deployments_report['report_platform_id'])
    else:
        return valid_fingerprints, invalid_fingerprints


def verify_report_fingerprints(deployments_report):
    """Verify that report fingerprints contain canonical facts.

    :param fingerprints: list of deployment report fingerprints
    :param report_platform_id: globally unique id for this report
    """
    fingerprints = deployments_report['system_fingerprints']
    report_platform_id = deployments_report['report_platform_id']

    valid_fingerprints = []
    invalid_fingerprints = []
    for fingerprint in fingerprints:
        found_facts = False
        for fact in CANONICAL_FACTS:
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
        LOG.warning(message % (report_platform_id, invalid_fingerprints))

    # Invalid fingerprints is for future use.
    return valid_fingerprints, invalid_fingerprints


def upload_to_host_inventory(account_number, fingerprints, report_platform_id):
    """
    Verify that the report contents are a valid deployments report.

    :param account_number: <str> of the User's account number.
    :param fingerprints: a list of dictionaries that have been validated.
    :param report_platform_id: <str> of the report platform id
    :returns True or False for if fingerprints is uploaded to inventory.
    """
    identity_string = '{"identity": {"account_number": "%s"}}' % str(account_number)
    bytes_string = identity_string.encode()
    x_rh_identity_value = base64.b64encode(bytes_string).decode()
    identity_header = {'x-rh-identity': x_rh_identity_value,
                       'Content-Type': 'application/json'}
    failed_fingerprints = []
    for fingerprint in fingerprints:
        body = {}
        body['account'] = account_number
        body['bios_uuid'] = fingerprint.get('bios_uuid')
        body['display_name'] = fingerprint.get('name')
        body['ip_addresses'] = fingerprint.get('ip_addresses')
        body['mac_addresses'] = fingerprint.get('mac_addresses')
        body['insights_id'] = fingerprint.get('insights_client_id')
        body['rhel_machine_id'] = fingerprint.get('etc_machine_id')
        body['subscription_manager_id'] = fingerprint.get('subscription_manager_id')
        body['fqdn'] = fingerprint.get('name')
        body['facts'] = [{
            'namespace': 'qpc',
            'facts': fingerprint
        }]
        try:
            response = requests.post(INSIGHTS_HOST_INVENTORY_URL,
                                     data=json.dumps(body),
                                     headers=identity_header)

            if response.status_code not in [HTTPStatus.OK, HTTPStatus.CREATED]:
                LOG.warning('Failed to add to host inventory '
                            '(account=%s, report_platform_id=%s, host_name=%s).  '
                            'Response json: %s',
                            account_number,
                            report_platform_id,
                            body.get('display_name'),
                            response.json())
                failed_fingerprints.append(fingerprint)
            else:
                LOG.debug('Success response from host inventory service (status=%s): %s',
                          response.status_code, response.json())

        except requests.exceptions.RequestException as err:
            failed_fingerprints.append(fingerprint)
            err_msg = 'Posting to (%s) returned error: %s'
            LOG.error(err_msg, INSIGHTS_HOST_INVENTORY_URL, err)

    successful = len(fingerprints) - len(failed_fingerprints)
    upload_msg = '%s/%s fingerprints from report %s were uploaded to the host inventory system.'
    if successful != len(fingerprints):
        LOG.warning(upload_msg, successful, len(fingerprints), report_platform_id)
    else:
        LOG.info(upload_msg, successful, len(fingerprints), report_platform_id)
    if failed_fingerprints:
        message = 'report_platform_id: "%s"| These fingerprints failed to upload to host inventory system: %s'
        LOG.debug(message, report_platform_id, failed_fingerprints)
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
        consumer_record = await MSG_PENDING_QUEUE.get()
        if consumer_record.topic == QPC_TOPIC:
            try:
                upload_service_message = unpack_consumer_record(consumer_record)
                message_hash = upload_service_message['hash']
                report_tar_gz = download_response_content(upload_service_message)
                qpc_deployments_report = extract_report_from_tar_gz(report_tar_gz)
                valid_fingerprints, invalid_fingerprints = \
                    verify_report_details(qpc_deployments_report)
                report_platform_id = qpc_deployments_report.get('report_platform_id')
                account_number = upload_service_message.get('rh_account')
                if not account_number:
                    raise QPCReportException('Message missing rh_account.')
                LOG.info('Report %s produce %s valid and %s invalid fingerprints for account %s. '
                         'Sending confirmation message to platform (kafka hash = %s)',
                         report_platform_id,
                         len(valid_fingerprints),
                         len(invalid_fingerprints),
                         account_number,
                         message_hash)
                await send_confirmation(message_hash, SUCCESS_CONFIRM_STATUS)
                upload_to_host_inventory(account_number,
                                         valid_fingerprints,
                                         report_platform_id)
            except QPCReportException as error:
                LOG.error('Error processing records.  Message: %s, Error: %s', consumer_record, error)
                await send_confirmation(message_hash, FAILURE_CONFIRM_STATUS)

        else:
            LOG.debug('Unexpected Message: %s', consumer_record)


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
