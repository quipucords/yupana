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
"""Kafka host inventory consumer."""

import asyncio
import json
import logging
import os
import threading

from aiokafka import AIOKafkaConsumer
from kafka.errors import ConnectionError as KafkaConnectionError

print('#' * 65)
print('Starting up test kakfa consumer')
print('#' * 65)

EVENT_LOOP = asyncio.get_event_loop()

# Topic to listen to
HOST_INVENTORY_INGESTION_KAFKA_TOPIC = os.getenv(
    'HOST_INVENTORY_INGESTION_KAFKA_TOPIC', 'platform.inventory.host-ingress')

# Insights Kafka messaging host
HOST_INVENTORY_INGESTION_KAFKA_HOST = os.getenv('HOST_INVENTORY_INGESTION_KAFKA_HOST', 'localhost')

# Insights Kafka messaging port
HOST_INVENTORY_INGESTION_KAFKA_PORT = os.getenv('HOST_INVENTORY_INGESTION_KAFKA_PORT', '29092')

# Insights Kafka server address
HOST_INVENTORY_INGESTION_KAFKA_ADDRESS = f'{HOST_INVENTORY_INGESTION_KAFKA_HOST}:{HOST_INVENTORY_INGESTION_KAFKA_PORT}'
print(HOST_INVENTORY_INGESTION_KAFKA_ADDRESS)


async def listen_for_messages(consumer):  # pragma: no cover
    """
    Listen for messages on the host inventory topic.

    Once a message from one of these topics arrives, we add
    them to the MSG_PENDING_QUEUE.
    :param consumer : Kafka consumer
    :returns None
    """
    try:
        await consumer.start()
    except KafkaConnectionError:
        await consumer.stop()
        print('Unable to connect to kafka server.  Closing consumer.')
        exit(1)

    print('Listener started.  Waiting for messages...')
    try:
        while True:
            # Consume messages
            async for consumer_record in consumer:
                print(consumer_record)
                await consumer.commit()
    finally:
        # Will leave consumer group; perform autocommit if enabled.
        await consumer.stop()


def asyncio_worker_thread(loop):  # pragma: no cover
    """
    Worker thread function to run the asyncio event loop.

    :param None
    :returns None
    """
    print('Initializing to listen on topic %s' % HOST_INVENTORY_INGESTION_KAFKA_TOPIC)
    consumer = AIOKafkaConsumer(
        HOST_INVENTORY_INGESTION_KAFKA_TOPIC,
        loop=EVENT_LOOP, bootstrap_servers=HOST_INVENTORY_INGESTION_KAFKA_ADDRESS,
        group_id='host-inventory-test-group', enable_auto_commit=False
    )

    loop.run_until_complete(listen_for_messages(consumer))


def initialize_kafka_handler():  # pragma: no cover
    """
    Create asyncio tasks and daemon thread to run event loop.

    :param None
    :returns None
    """
    event_loop_thread = threading.Thread(target=asyncio_worker_thread, args=(EVENT_LOOP,))
    event_loop_thread.start()


print('Initializing the kafka messaging handler.')
initialize_kafka_handler()
