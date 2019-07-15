#
# Copyright 2019 Red Hat, Inc.
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
"""Host validation consumer."""

import asyncio
import json
import logging
import threading

from aiokafka import AIOKafkaConsumer
from processor.report_consumer import (KafkaMsgHandlerError,
                                       QPCKafkaMsgException,
                                       format_message,
                                       listen_for_messages)

from api.serializers import InventoryUploadErrorSerializer
from config.settings.base import INSIGHTS_KAFKA_ADDRESS

LOG = logging.getLogger(__name__)
HOST_VALIDATION_CONSUMER_LOOP = asyncio.get_event_loop()
VALIDATION_PENDING_QUEUE = asyncio.Queue()
VALIDATION_TOPIC = 'platform.upload.hostvalidation'


def unpack_validation_msg(host_validation_message):
    """Decode and return the incoming validation kafka message.

    :param host_validation_message: the value of the kakfa message from the
        host inventory service.
    :returns: the decoded JSON message
    """
    prefix = 'UNPACKING VALIDATION MESSAGE'
    try:
        json_message = json.loads(host_validation_message.value.decode('utf-8'))
        message = 'received on %s topic' % host_validation_message.topic
        LOG.info(format_message(prefix,
                                message))
        LOG.debug(format_message(
            prefix,
            'Message: %s' % str(host_validation_message)))
        return json_message
    except ValueError:
        raise QPCKafkaMsgException(format_message(prefix, 'Host validation message not JSON.'))


def record_host_results(validation_message):
    """Record host validation errors.

    :param validation_message: JSON message containing error info.
    """
    LOG.debug('Validation message contents: %s ', validation_message)
    facts = validation_message.get('facts', [])
    yupana_namespaced_facts = None
    for namespaced_facts in facts:
        if namespaced_facts.get('namespace', '') == 'yupana':
            yupana_namespaced_facts = namespaced_facts
            break
    if yupana_namespaced_facts:
        yupana_facts = yupana_namespaced_facts.get('facts', {})
        report_platform_id = yupana_facts.get('report_platform_id')
        report_slice_id = yupana_facts.get('report_slice_id')
        account = yupana_facts.get('account')
        source = yupana_facts.get('source')
        yupana_host_id = yupana_facts.get('yupana_host_id')
        # print('yupana_facts: ')
        # print(yupana_facts)
        # print('report platform id: %s' % report_platform_id)
        # print('report_slice_id: %s' % report_slice_id)
        # print('account: %s' % account)
        # print('source: %s' % source)
        # print('yupana_host_id: %s' % yupana_host_id)
        upload_error = {
            'report_slice_id': report_slice_id,
            'report_platform_id': report_platform_id,
            'account': account,
            'source': source,
            'yupana_host_id': yupana_host_id,
            'details': json.dumps(validation_message)
        }
        upload_serializer = InventoryUploadErrorSerializer(data=upload_error)
        upload_serializer.is_valid(raise_exception=True)
        upload_serializer.save()


async def process_consumer_messages(consumer, consumer_record):
    """Attempt to process the host validation message."""
    prefix = 'HOST VALIDATION RECIEVED'
    if consumer_record.topic == VALIDATION_TOPIC:
        try:
            validation_message = unpack_validation_msg(consumer_record)
            record_host_results(validation_message)
        except QPCKafkaMsgException as message_error:
            LOG.error(format_message(
                prefix, 'Error processing validation message.  Message: %s, Error: %s' %
                (consumer_record, message_error)))
            await consumer.commit()
    else:
        LOG.debug(format_message(
            prefix, 'Message not on %s topic: %s' % (VALIDATION_TOPIC, consumer_record)))


async def loop_process_consumer_messages(consumer):
    """Loop the process_consumer_messages function."""
    while True:
        consumer_record = await VALIDATION_PENDING_QUEUE.get()
        await process_consumer_messages(consumer, consumer_record)


def create_host_validation_consumer_loop(loop):  # pragma: no cover
    """
    Worker thread function to run the asyncio event loop.

    :param None
    :returns None
    """
    consumer = AIOKafkaConsumer(
        VALIDATION_TOPIC,
        loop=HOST_VALIDATION_CONSUMER_LOOP, bootstrap_servers=INSIGHTS_KAFKA_ADDRESS,
        group_id='qpc-group', enable_auto_commit=False
    )

    loop.create_task(loop_process_consumer_messages(consumer))

    try:
        log_message = 'Host validation listener started.  Waiting for messages...'
        loop.run_until_complete(listen_for_messages(
            consumer, VALIDATION_PENDING_QUEUE, log_message))
    except KafkaMsgHandlerError as err:
        LOG.info('Stopping kafka worker thread.  Error: %s', str(err))


def initialize_host_validation_consumer():  # pragma: no cover
    """
    Create asyncio tasks and daemon thread to run event loop.

    :param None
    :returns None
    """
    event_loop_thread = threading.Thread(
        target=create_host_validation_consumer_loop,
        args=(HOST_VALIDATION_CONSUMER_LOOP,))
    event_loop_thread.daemon = True
    event_loop_thread.start()
