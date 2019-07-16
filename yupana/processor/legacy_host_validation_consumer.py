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
# marking this as deprecated because it is undecided how the host inventory
# will handle validation

import asyncio
import json
import logging
import threading

from aiokafka import AIOKafkaConsumer
from processor.report_consumer import (KafkaMsgHandlerError,
                                       QPCKafkaMsgException,
                                       format_message,
                                       listen_for_messages)

from config.settings.base import INSIGHTS_KAFKA_ADDRESS

LOG = logging.getLogger(__name__)
HOST_VALIDATION_CONSUMER_LOOP = asyncio.get_event_loop()
VALIDATION_PENDING_QUEUE = asyncio.Queue()
VALIDATION_TOPIC = 'platform.inventory.host-ingress'


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
    LOG.info('Validation message contents: %s ', validation_message)
    # in the future we will save an inventory upload error


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
