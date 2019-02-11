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
# we will want to revert these to the defaults when we improve processing/ACK time in yupana
SESSION_TIMEOUT = 300 * 1000
REQUEST_TIMEOUT = 500 * 1000


def format_message(prefix, message, account_number=None, report_id=None):
    """Format log messages in a consistent way.

    :param prefix: (str) A meaningful prefix to be displayed in all caps.
    :param message: (str) A short message describing the state
    :param account_number: (str) The account sending the report.
    :param report_id: (str) The qpc report id.
    :returns: (str) containing formatted message
    """
    if not report_id and not account_number:
        actual_message = 'Report %s - %s' % (prefix, message)
    elif account_number and not report_id:
        actual_message = 'Report(account=%s) %s - %s' % (account_number, prefix, message)
    else:
        actual_message = 'Report(account=%s, report_id=%s) %s - %s' % (account_number, report_id, prefix, message)

    return actual_message


class QPCReportException(Exception):
    """Use to report errors during qpc report processing."""

    pass


class QPCKafkaMsgException(Exception):
    """Use to report errors with kafka message.

    Used when we think the kafka message is useful
    in debugging.  Error with external services
    (connected via kafka).
    """

    pass


class KafkaMsgHandlerError(Exception):
    """Kafka msg handler error."""

    pass


def unpack_consumer_record(upload_service_message):
    """Retrieve report URL from kafka.

    :param upload_service_message: the value of the kakfa message from file
        upload service.
    :returns: str containing the url to the qpc report's tar.gz file.
    """
    prefix = 'NEW REPORT UPLOAD'
    try:
        json_message = json.loads(upload_service_message.value.decode('utf-8'))
        message = 'received on %s topic' % upload_service_message.topic
        account_number = json_message.get('rh_account')
        LOG.info(format_message(prefix,
                                message,
                                account_number=account_number))
        LOG.debug(format_message(
            prefix,
            'Message: %s' % str(upload_service_message),
            account_number=account_number))
        return json_message
    except ValueError:
        raise QPCKafkaMsgException(format_message(prefix, 'Upload service message not JSON.'))


def download_report(account_number, upload_service_message):
    """
    Download report.

    :param account_number: <str> of the User's account number.
    :param upload_service_message: the value of the kakfa message
    :returns content: The tar.gz binary content or None if there are errors.
    """
    prefix = 'REPORT DOWNLOAD'
    try:
        report_url = upload_service_message.get('url', None)
        if not report_url:
            raise QPCReportException(
                format_message(
                    prefix,
                    'kafka message missing report url.  Message: %s' % upload_service_message,
                    account_number=account_number))

        LOG.info(format_message(prefix, 'downloading %s' % report_url, account_number=account_number
                                ))
        download_response = requests.get(report_url)
        if download_response.status_code != HTTPStatus.OK:
            raise QPCKafkaMsgException(
                format_message(prefix,
                               'HTTP status code %s returned for URL %s.  Message: %s' % (
                                   download_response.status_code,
                                   report_url,
                                   upload_service_message),
                               account_number=account_number))

        LOG.info(format_message(
            prefix,
            'successfully downloaded %s' % report_url,
            account_number=account_number
        ))
        return download_response.content
    except requests.exceptions.HTTPError as err:
        raise QPCReportException(
            format_message(prefix,
                           'Unexpected http error for URL %s. Error: %s' % (
                               report_url,
                               err),
                           account_number=account_number))


def extract_report_from_tar_gz(account_number, report_tar_gz):
    """Extract Insight report from tar.gz file.

    :param account_number: the account number associated with report
    :param report_tar_gz: A hexstring or BytesIO tarball
        saved in memory with gzip compression.
    :returns: Insight report as dict
    """
    prefix = 'EXTRACT REPORT FROM TAR'
    try:
        tar = tarfile.open(fileobj=BytesIO(report_tar_gz), mode='r:gz')
        files_check = tar.getmembers()
        json_files = []
        for file in files_check:
            if '.json' in file.name:
                json_files.append(file)
        if len(json_files) > 1:
            raise QPCReportException(
                format_message(prefix,
                               'tar.gz contains multiple files.',
                               account_number=account_number))
        if json_files:
            file = json_files[0]
            tarfile_obj = tar.extractfile(file)
            report_json_str = tarfile_obj.read().decode('utf-8')
            try:
                Insight_report = json.loads(report_json_str)
                LOG.info(
                    format_message(
                        prefix, 'successful',
                        account_number=account_number,
                        report_id=Insight_report.get('report_platform_id')))
                return Insight_report
            except ValueError as error:
                raise QPCReportException(
                    format_message(prefix,
                                   'Report not JSON. Error: %s' % str(error),
                                   account_number=account_number))
        raise QPCReportException(
            format_message(prefix,
                           'Tar contains no JSON files.',
                           account_number=account_number))
    except QPCReportException as qpc_err:
        raise qpc_err
    except Exception as err:
        raise QPCReportException(
            format_message(prefix,
                           'Unexpected error reading tar.gz: %s' % str(err),
                           account_number=account_number))


def verify_report_details(account_number, insights_report):
    """
    Verify that the report contents are a valid Insights report.

    :param account_number: the account number associated with report
    :param insights_report: dict with report
    :returns: tuple contain list of valid and invalid hosts
    """
    prefix = 'VERIFY REPORT STRUCTURE'
    required_keys = ['report_platform_id',
                     'report_id',
                     'report_version',
                     'report_type',
                     'hosts']
    report_id = insights_report.get('report_platform_id')
    missing_keys = []
    for key in required_keys:
        required_key = insights_report.get(key)
        if not required_key:
            missing_keys.append(key)

    if missing_keys:
        missing_keys_str = ', '.join(missing_keys)
        raise QPCReportException(
            format_message(
                prefix,
                'Report is missing required fields: %s.' % missing_keys_str,
                account_number=account_number,
                report_id=report_id))

    if insights_report['report_type'] != 'insights':
        raise QPCReportException(
            format_message(
                prefix,
                'Invalid report_type: %s' % insights_report['report_type'],
                account_number=account_number,
                report_id=report_id))

    valid_hosts, invalid_hosts = verify_report_hosts(account_number, insights_report)
    number_valid = len(valid_hosts)
    total = number_valid + len(invalid_hosts)
    LOG.info(format_message(
        prefix,
        '%s/%s are valid hosts' % (
            number_valid, total),
        account_number=account_number,
        report_id=report_id
    ))
    if not valid_hosts:
        raise QPCReportException(
            format_message(
                prefix,
                'contains no valid hosts.',
                account_number=account_number,
                report_id=report_id))
    else:
        return valid_hosts, invalid_hosts


def verify_report_hosts(account_number, insights_report):
    """Verify that report hosts contain canonical facts.

    :param account_number: the account number associated with report
    :param insights_report: dict with report
    """
    hosts = insights_report['hosts']
    report_id = insights_report['report_platform_id']

    prefix = 'VALIDATE hosts'
    valid_hosts = {}
    invalid_hosts = {}
    for host_id, host in hosts.items():
        found_facts = False
        for fact in CANONICAL_FACTS:
            if host.get(fact):
                found_facts = True
                break
        if found_facts:
            valid_hosts[host_id] = host
        else:
            host.pop('metadata', None)
            invalid_hosts[host_id] = host
    if invalid_hosts:
        LOG.warning(
            format_message(
                prefix,
                'Removed %d hosts with 0 canonical facts: %s' % (
                    len(invalid_hosts), invalid_hosts),
                account_number=account_number,
                report_id=report_id))

    # Invalid hosts is for future use.
    return valid_hosts, invalid_hosts


async def send_confirmation(file_hash, status, account_number=None, report_id=None):  # pragma: no cover
    """
    Send kafka validation message to Insights Upload service.

    When a new file lands for topic 'qpc' we must validate it
    so that it will be made permanently available to other
    apps listening on the 'available' topic.
    :param: file_hash (String): Hash for file being confirmed.
    :param: status (String): Either 'success' or 'failure'
    :param account_number: <str> of the User's account number.
    :param report_id: <str> of the report platform id
    :returns None
    """
    prefix = 'REPORT VALIDATION STATE ON KAFKA'
    producer = AIOKafkaProducer(
        loop=EVENT_LOOP, bootstrap_servers=INSIGHTS_KAFKA_ADDRESS
    )
    try:
        await producer.start()
    except (KafkaConnectionError, TimeoutError):
        await producer.stop()
        raise KafkaMsgHandlerError(
            format_message(
                prefix,
                'Unable to connect to kafka server.  Closing producer.',
                account_number=account_number,
                report_id=report_id))
    try:
        validation = {
            'hash': file_hash,
            'validation': status
        }
        msg = bytes(json.dumps(validation), 'utf-8')
        await producer.send_and_wait(VALIDATION_TOPIC, msg)
        LOG.info(
            format_message(
                prefix,
                'Send %s validation status to file upload on kafka' % status,
                account_number=account_number,
                report_id=report_id))
    finally:
        await producer.stop()


def upload_to_host_inventory(account_number, report_id, hosts):
    """
    Verify that the report contents are a valid Insights report.

    :param account_number: <str> of the User's account number.
    :param report_id: <str> of the report platform id
    :param hosts: a list of dictionaries that have been validated.
    :returns None
    """
    prefix = 'UPLOAD TO HOST INVENTORY'
    identity_string = '{"identity": {"account_number": "%s"}}' % str(account_number)
    bytes_string = identity_string.encode()
    x_rh_identity_value = base64.b64encode(bytes_string).decode()
    identity_header = {'x-rh-identity': x_rh_identity_value,
                       'Content-Type': 'application/json'}
    failed_hosts = []
    for host_id, host in hosts.items():
        body = {}
        body['account'] = account_number
        body['bios_uuid'] = host.get('bios_uuid')
        body['display_name'] = host.get('name')
        body['ip_addresses'] = host.get('ip_addresses')
        body['mac_addresses'] = host.get('mac_addresses')
        body['insights_id'] = host.get('insights_client_id')
        body['rhel_machine_id'] = host.get('etc_machine_id')
        body['subscription_manager_id'] = host.get('subscription_manager_id')
        body['fqdn'] = host.get('name')
        body['facts'] = [{
            'namespace': 'qpc',
            'facts': host
        }]
        try:
            response = requests.post(INSIGHTS_HOST_INVENTORY_URL,
                                     data=json.dumps(body),
                                     headers=identity_header)

            if response.status_code not in [HTTPStatus.OK, HTTPStatus.CREATED]:
                try:
                    json_body = response.json()
                except ValueError:
                    json_body = 'No JSON response'

                failed_hosts.append(
                    {
                        'status_code': response.status_code,
                        'error': json_body,
                        'display_name': body.get('display_name'),
                        'host': host})

        except requests.exceptions.RequestException as err:
            failed_hosts.append(
                {
                    'status_code': 'None',
                    'error': str(err),
                    'display_name': body.get('display_name'),
                    'host': host})

    successful = len(hosts) - len(failed_hosts)
    upload_msg = format_message(
        prefix, '%s/%s hosts uploaded to host inventory' %
        (successful, len(hosts)),
        account_number=account_number,
        report_id=report_id
    )
    if successful != len(hosts):
        LOG.warning(upload_msg)
    else:
        LOG.info(upload_msg)
    if failed_hosts:
        for failed_info in failed_hosts:
            LOG.error(format_message(
                prefix,
                'Host inventory returned %s for %s.  Error: %s.  host: %s' % (
                    failed_info.get('status_code'),
                    failed_info.get('display_name'),
                    failed_info.get('error'),
                    failed_info.get('host')),
                account_number=account_number,
                report_id=report_id
            ))


async def process_messages():  # pragma: no cover
    """
    Process asyncio MSG_PENDING_QUEUE and send validation status.

    :param None
    :returns None
    """
    prefix = 'PROCESS MESSAGE'
    while True:
        consumer_record = await MSG_PENDING_QUEUE.get()
        if consumer_record.topic == QPC_TOPIC:
            try:
                upload_service_message = unpack_consumer_record(consumer_record)
                account_number = upload_service_message.get('rh_account')
                if not account_number:
                    raise QPCKafkaMsgException(
                        format_message(
                            prefix,
                            'Message missing rh_account.'))
                try:
                    message_hash = upload_service_message['hash']
                    report_tar_gz = download_report(account_number, upload_service_message)
                    qpc_insights_report = extract_report_from_tar_gz(account_number, report_tar_gz)
                    valid_hosts, _ = verify_report_details(
                        account_number, qpc_insights_report)
                    report_id = qpc_insights_report.get('report_platform_id')
                    await send_confirmation(message_hash,
                                            SUCCESS_CONFIRM_STATUS,
                                            account_number=account_number,
                                            report_id=report_id)
                    upload_to_host_inventory(account_number,
                                             report_id,
                                             valid_hosts)
                except QPCReportException as error:
                    LOG.error(error)
                    await send_confirmation(message_hash, FAILURE_CONFIRM_STATUS, account_number=account_number)
            except QPCKafkaMsgException as message_error:
                LOG.error(prefix, 'Error processing records.  Message: %s, Error: %s', consumer_record, message_error)
                await send_confirmation(message_hash, FAILURE_CONFIRM_STATUS)

        else:
            LOG.debug(format_message(prefix,
                                     'Message not on %s topic: %s' % (
                                         QPC_TOPIC, consumer_record)))


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
        group_id='qpc-group', session_timeout_ms=SESSION_TIMEOUT,
        request_timeout_ms=REQUEST_TIMEOUT
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
