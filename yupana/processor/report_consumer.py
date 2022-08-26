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
"""ReportConsumer class for saving & acking uploaded messages."""

import asyncio
import json
import logging
import threading
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytz
from aiokafka import AIOKafkaConsumer
from kafka.errors import KafkaConnectionError
from processor.processor_utils import (PROCESSOR_INSTANCES,
                                       UPLOAD_REPORT_CONSUMER_LOOP,
                                       format_message,
                                       print_error_loop_event)
from prometheus_client import Counter

from api.models import Report
from api.serializers import ReportSerializer
from config.settings.base import INSIGHTS_KAFKA_ADDRESS, QPC_TOPIC, kafka_ssl_config

LOG = logging.getLogger(__name__)

REPORT_PENDING_QUEUE = asyncio.Queue()

MSG_UPLOADS = Counter('yupana_message_uploads',
                      'Number of messages uploaded to qpc topic',
                      ['org_id'])


KAFKA_ERRORS = Counter('yupana_kafka_errors', 'Number of Kafka errors')
DB_ERRORS = Counter('yupana_db_errors', 'Number of db errors')
PROCESSOR_NAME = 'report_consumer'


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


class ReportConsumer():
    """Class for saving and acking uploaded reports."""

    def __init__(self):
        """Create a report consumer."""
        self.processor_name = PROCESSOR_NAME
        self.should_run = True
        self.prefix = 'REPORT CONSUMER'
        self.account_number = None
        self.org_id = None
        self.upload_message = None
        kafka_ssl = kafka_ssl_config()
        self.consumer = AIOKafkaConsumer(
            QPC_TOPIC,
            loop=UPLOAD_REPORT_CONSUMER_LOOP, bootstrap_servers=INSIGHTS_KAFKA_ADDRESS,
            group_id='qpc-group', enable_auto_commit=False,
            security_protocol=kafka_ssl.get('security_protocol', 'PLAINTEXT'),
            ssl_context=kafka_ssl.get('ssl_context', None),
            sasl_mechanism=kafka_ssl.get('sasl_mechanism', 'PLAIN'),
            sasl_plain_username=kafka_ssl.get('sasl_plain_username', None),
            sasl_plain_password=kafka_ssl.get('sasl_plain_password', None)
        )

    @KAFKA_ERRORS.count_exceptions()
    def run(self, loop):
        """Worker thread function to run the asyncio event loop.

        :param None
        :returns None
        """
        loop.create_task(self.loop_save_message_and_ack())

        try:
            log_message = 'Upload report listener started.  Waiting for messages...'
            loop.run_until_complete(self.listen_for_messages(REPORT_PENDING_QUEUE, log_message))
        except KafkaMsgHandlerError as err:
            KAFKA_ERRORS.inc()
            LOG.info('Stopping kafka worker thread.  Error: %s', str(err))
        except Exception:  # pylint: disable=broad-except
            pass

    async def loop_save_message_and_ack(self):
        """Run the report consumer in a loop."""
        while self.should_run:
            consumer_record = await REPORT_PENDING_QUEUE.get()
            await self.save_message_and_ack(consumer_record)

    async def save_message_and_ack(self, consumer_record):  # noqa: C901 (too-complex)
        """Save and ack the uploaded kafka message."""
        self.prefix = 'SAVING MESSAGE'
        if consumer_record.topic == QPC_TOPIC:
            try:
                missing_fields = []
                self.upload_message = self.unpack_consumer_record(consumer_record)
                # rh_account is being deprecated so we use it as a backup if
                # account is not there
                rh_account = self.upload_message.get('rh_account')
                request_id = self.upload_message.get('request_id')
                url = self.upload_message.get('url')
                self.org_id = self.upload_message.get('org_id')
                self.account_number = self.upload_message.get('account', rh_account)
                if not self.org_id:
                    missing_fields.append('org_id')
                if not request_id:
                    missing_fields.append('request_id')
                if not url:
                    missing_fields.append('url')
                if missing_fields:
                    raise QPCKafkaMsgException(
                        format_message(
                            self.prefix,
                            'Message missing required field(s): %s.' % ', '.join(missing_fields)))
                self.check_if_url_expired(url, request_id)
                try:
                    uploaded_report = {
                        'upload_srv_kafka_msg': json.dumps(self.upload_message),
                        'org_id': self.org_id,
                        'request_id': request_id,
                        'state': Report.NEW,
                        'state_info': json.dumps([Report.NEW]),
                        'last_update_time': datetime.now(pytz.utc),
                        'arrival_time': datetime.now(pytz.utc),
                        'retry_count': 0
                    }
                    if self.account_number:
                        uploaded_report['account'] = self.account_number
                    report_serializer = ReportSerializer(data=uploaded_report)
                    report_serializer.is_valid(raise_exception=True)
                    report_serializer.save()
                    MSG_UPLOADS.labels(org_id=self.org_id).inc()
                    LOG.info(format_message(
                        self.prefix,
                        'Upload service message saved with request_id: %s. Ready for processing.'
                        % request_id))
                    await self.consumer.commit()
                except Exception as error:  # pylint: disable=broad-except
                    DB_ERRORS.inc()
                    LOG.error(format_message(
                        self.prefix,
                        'The following error occurred while trying to save and '
                        'commit the message: %s' % error))
                    print_error_loop_event()
            except QPCKafkaMsgException as message_error:
                LOG.error(format_message(
                    self.prefix, 'Error processing records.  Message: %s, Error: %s' %
                    (consumer_record, message_error)))
                await self.consumer.commit()
        else:
            LOG.debug(format_message(
                self.prefix, 'Message not on %s topic: %s' % (QPC_TOPIC, consumer_record)))

    def check_if_url_expired(self, url, request_id):
        """Validate if url is expired."""
        self.prefix = 'NEW REPORT VALIDATION'
        parsed_url_query = parse_qs(urlparse(url).query)
        creation_timestamp = parsed_url_query['X-Amz-Date']
        expire_time = timedelta(seconds=int(parsed_url_query['X-Amz-Expires'][0]))
        creation_datatime = datetime.strptime(str(creation_timestamp[0]), '%Y%m%dT%H%M%SZ')
        if datetime.now().replace(microsecond=0) > (creation_datatime + expire_time):
            raise QPCKafkaMsgException(
                format_message(self.prefix,
                               'Request_id = %s is already expired and cannot be processed:'
                               'Creation time = %s, Expiry interval = %s.'
                               % (request_id, creation_datatime, expire_time)))

    def unpack_consumer_record(self, consumer_record):
        """Decode the uploaded message and return it in JSON format."""
        self.prefix = 'NEW REPORT UPLOAD'
        try:
            json_message = json.loads(consumer_record.value.decode('utf-8'))
            message = 'received on %s topic' % consumer_record.topic
            # rh_account is being deprecated so we use it as a backup if
            # account is not there
            rh_account = json_message.get('rh_account')
            self.account_number = json_message.get('account', rh_account)
            LOG.info(format_message(self.prefix,
                                    message,
                                    account_number=self.account_number,
                                    org_id=self.org_id))
            LOG.debug(format_message(
                self.prefix,
                'Message: %s' % str(consumer_record),
                account_number=self.account_number,
                org_id=self.org_id))
            return json_message
        except ValueError:
            raise QPCKafkaMsgException(format_message(
                self.prefix, 'Upload service message not JSON.'))

    @KAFKA_ERRORS.count_exceptions()
    async def listen_for_messages(self, async_queue, log_message):
        """Listen for messages on the qpc topic.

        Once a message from one of these topics arrives, we add
        them to the passed in queue.
        :param consumer : Kafka consumer
        :returns None
        """
        try:
            await self.consumer.start()
        except KafkaConnectionError:
            KAFKA_ERRORS.inc()
            print_error_loop_event()
            raise KafkaMsgHandlerError('Unable to connect to kafka server.')
        except Exception as err:  # pylint: disable=broad-except
            KAFKA_ERRORS.inc()
            LOG.error(format_message(
                self.prefix, 'The following error occurred: %s' % err))
            print_error_loop_event()

        LOG.info(log_message)
        try:
            # Consume messages
            async for msg in self.consumer:
                service = dict(msg.headers or []).get('service')
                if service:
                    service = service.decode('utf-8')
                    if service == 'qpc':
                        await async_queue.put(msg)
        except Exception as err:  # pylint: disable=broad-except
            KAFKA_ERRORS.inc()
            LOG.error(format_message(
                self.prefix, 'The following error occurred: %s' % err))
            print_error_loop_event()
        finally:
            # Will leave consumer group; perform autocommit if enabled.
            await self.consumer.stop()


def create_upload_report_consumer_loop(loop):
    """Initialize the report consumer class and run."""
    report_consumer = ReportConsumer()
    PROCESSOR_INSTANCES.append(report_consumer)
    report_consumer.run(loop)


def initialize_upload_report_consumer():  # pragma: no cover
    """
    Create asyncio tasks and daemon thread to run event loop.

    :param None
    :returns None
    """
    event_loop_thread = threading.Thread(
        target=create_upload_report_consumer_loop, name=PROCESSOR_NAME,
        args=(UPLOAD_REPORT_CONSUMER_LOOP,))
    event_loop_thread.daemon = True
    event_loop_thread.start()
