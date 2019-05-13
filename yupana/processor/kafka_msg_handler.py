#
# Copyright 2018-2019 Red Hat, Inc.
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
import threading
from datetime import datetime

import pytz
from aiokafka import AIOKafkaConsumer
from kafka.errors import ConnectionError as KafkaConnectionError
from prometheus_client import Counter

from api.models import Report
from api.serializers import ReportSerializer
from config.settings.base import INSIGHTS_KAFKA_ADDRESS

LOG = logging.getLogger(__name__)
EVENT_LOOP = asyncio.get_event_loop()
MSG_PENDING_QUEUE = asyncio.Queue()
QPC_TOPIC = 'platform.upload.qpc'
AVAILABLE_TOPIC = 'platform.upload.available'

MSG_UPLOADS = Counter('uploaded_messages', 'Number of messages uploaded to qpc topic')


def format_message(prefix, message, account_number=None,
                   report_platform_id=None):
    """Format log messages in a consistent way.

    :param prefix: (str) A meaningful prefix to be displayed in all caps.
    :param message: (str) A short message describing the state
    :param account_number: (str) The account sending the report.
    :param report_platform_id: (str) The qpc report id.
    :returns: (str) containing formatted message
    """
    if not report_platform_id and not account_number:
        actual_message = 'Report %s - %s' % (prefix, message)
    elif account_number and not report_platform_id:
        actual_message = 'Report(account=%s) %s - %s' % (account_number, prefix, message)
    else:
        actual_message = 'Report(account=%s, report_platform_id=%s) %s - %s' % (
            account_number,
            report_platform_id, prefix,
            message)

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


async def save_message_and_ack(consumer, consumer_record):
    """Save and ack the kafka uploaded message."""
    prefix = 'SAVING MESSAGE'
    if consumer_record.topic == QPC_TOPIC:
        try:
            upload_service_message = unpack_consumer_record(consumer_record)
            rh_account = upload_service_message.get('rh_account')
            if not rh_account:
                raise QPCKafkaMsgException(
                    format_message(
                        prefix,
                        'Message missing rh_account.'))
            try:
                uploaded_report = {
                    'upload_srv_kafka_msg': json.dumps(upload_service_message),
                    'rh_account': rh_account,
                    'state': Report.NEW,
                    'state_info': json.dumps([Report.NEW]),
                    'last_update_time': datetime.now(pytz.utc),
                    'retry_count': 0
                }
                report_serializer = ReportSerializer(data=uploaded_report)
                if report_serializer.is_valid(raise_exception=True):
                    report_serializer.save()
                MSG_UPLOADS.inc()
                LOG.info(format_message(
                    prefix, 'Upload service message saved. Ready for processing.'))
                await consumer.commit()
            except Exception as error:  # pylint: disable=broad-except
                LOG.error(format_message(
                    prefix,
                    'The following error occurred while trying to save and '
                    'commit the message: %s' % error))
        except QPCKafkaMsgException as message_error:
            LOG.error(format_message(
                prefix, 'Error processing records.  Message: %s, Error: %s',
                consumer_record, message_error))
            await consumer.commit()
    else:
        LOG.debug(format_message(
            prefix, 'Message not on %s topic: %s' % (QPC_TOPIC, consumer_record)))


async def loop_save_message_and_ack(consumer):
    """Loop the save_message_and_ack function."""
    while True:
        consumer_record = await MSG_PENDING_QUEUE.get()
        await save_message_and_ack(consumer, consumer_record)


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
        group_id='qpc-group', enable_auto_commit=False
    )

    loop.create_task(loop_save_message_and_ack(consumer))

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
