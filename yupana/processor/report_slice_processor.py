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
"""Report Slice Processor."""

import asyncio
import json
import logging
import threading

from aiokafka import AIOKafkaProducer
from kafka.errors import ConnectionError as KafkaConnectionError
from processor.abstract_processor import (AbstractProcessor, FAILED_TO_VALIDATE)
from processor.processor_utils import (PROCESSOR_INSTANCES,
                                       SLICE_PROCESSING_LOOP,
                                       format_message,
                                       stop_all_event_loops)
from processor.report_consumer import (KAFKA_ERRORS,
                                       KafkaMsgHandlerError,
                                       QPCReportException)

from api.models import ReportSlice
from api.serializers import ReportSliceSerializer
from config.settings.base import (HOSTS_UPLOAD_FUTURES_COUNT,
                                  HOSTS_UPLOAD_TIMEOUT,
                                  HOST_INVENTORY_UPLOAD_MODE,
                                  INSIGHTS_KAFKA_ADDRESS,
                                  RETRIES_ALLOWED,
                                  RETRY_TIME)

LOG = logging.getLogger(__name__)

HOSTS_UPLOAD_FUTURES_COUNT = int(HOSTS_UPLOAD_FUTURES_COUNT)
HOSTS_UPLOAD_TIMEOUT = int(HOSTS_UPLOAD_TIMEOUT)
FAILED_UPLOAD = 'UPLOAD'
RETRIES_ALLOWED = int(RETRIES_ALLOWED)
RETRY_TIME = int(RETRY_TIME)
UPLOAD_TOPIC = 'platform.inventory.host-ingress'  # placeholder topic


class RetryUploadTimeException(Exception):
    """Use to report upload errors that should be retried on time."""

    pass


class RetryUploadCommitException(Exception):
    """Use to report upload errors that should be retried on commit."""

    pass


class ReportSliceProcessor(AbstractProcessor):  # pylint: disable=too-many-instance-attributes
    """Class for processing report slices that have been created."""

    def __init__(self):
        """Create a report slice state machine."""
        state_functions = {
            ReportSlice.RETRY_VALIDATION: self.transition_to_validated,
            ReportSlice.NEW: self.transition_to_started,
            ReportSlice.STARTED: self.transition_to_hosts_uploaded,
            ReportSlice.VALIDATED: self.transition_to_hosts_uploaded,
            ReportSlice.HOSTS_UPLOADED: self.archive_report_and_slices,
            ReportSlice.FAILED_VALIDATION: self.archive_report_and_slices,
            ReportSlice.FAILED_HOSTS_UPLOAD: self.archive_report_and_slices}
        state_metrics = {
            ReportSlice.FAILED_VALIDATION: FAILED_TO_VALIDATE
        }
        self.producer = AIOKafkaProducer(
            loop=SLICE_PROCESSING_LOOP, bootstrap_servers=INSIGHTS_KAFKA_ADDRESS
        )
        super().__init__(pre_delegate=self.pre_delegate,
                         state_functions=state_functions,
                         state_metrics=state_metrics,
                         async_states=[ReportSlice.STARTED, ReportSlice.VALIDATED],
                         object_prefix='REPORT SLICE',
                         object_class=ReportSlice,
                         object_serializer=ReportSliceSerializer
                         )

    def pre_delegate(self):
        """Call the correct function based on report slice state.

        If the function is async, make sure to await it.
        """
        self.state = self.report_or_slice.state
        self.account_number = self.report_or_slice.account
        if self.report_or_slice.candidate_hosts:
            self.candidate_hosts = json.loads(self.report_or_slice.candidate_hosts)
        if self.report_or_slice.failed_hosts:
            self.failed_hosts = json.loads(self.report_or_slice.failed_hosts)
        if self.report_or_slice.report_json:
            self.report_json = json.loads(self.report_or_slice.report_json)
        if self.report_or_slice.report_platform_id:
            self.report_platform_id = self.report_or_slice.report_platform_id
        if self.report_or_slice.report_slice_id:
            self.report_slice_id = self.report_or_slice.report_slice_id

    def transition_to_validated(self):
        """Revalidate the slice because it is in the failed validation state."""
        self.prefix = 'ATTEMPTING VALIDATION'
        LOG.info(format_message(
            self.prefix,
            'Uploading hosts to inventory. State is "%s".' % self.report_or_slice.state,
            account_number=self.account_number, report_platform_id=self.report_platform_id))
        try:
            self.report_json = json.loads(self.report_or_slice.report_json)
            self.candidate_hosts = self._validate_report_details()
            # Here we want to update the report state of the actual report slice & when finished
            self.next_state = ReportSlice.VALIDATED
            options = {'candidate_hosts': self.candidate_hosts}
            self.update_object_state(options=options)
        except QPCReportException:
            # if any QPCReportExceptions occur, we know that the report is not valid but has been
            # successfully validated
            # that means that this slice is invalid and only awaits being archived
            self.next_state = ReportSlice.FAILED_VALIDATION
            self.update_object_state(options={})
        except Exception as error:  # pylint: disable=broad-except
            # This slice blew up validation - we want to retry it later,
            # which means it enters our odd state
            # of requiring validation
            LOG.error(format_message(self.prefix, 'The following error occurred: %s.' % str(error)))
            self.determine_retry(ReportSlice.FAILED_VALIDATION, ReportSlice.RETRY_VALIDATION,
                                 retry_type=ReportSlice.GIT_COMMIT)

    async def transition_to_hosts_uploaded(self):
        """Upload the host candidates to inventory & move to hosts_uploaded state."""
        self.prefix = 'ATTEMPTING HOST UPLOAD'
        LOG.info(format_message(
            self.prefix,
            'Uploading hosts to inventory. State is "%s". HOST_INVENTORY_UPLOAD_MODE is "%s".' %
            (self.report_or_slice.state, HOST_INVENTORY_UPLOAD_MODE),
            account_number=self.account_number, report_platform_id=self.report_platform_id))
        try:
            if self.candidate_hosts:
                candidates = self.generate_upload_candidates()
                await self._upload_to_host_inventory_via_kafka(candidates)
                LOG.info(format_message(self.prefix, 'All hosts were successfully uploaded.',
                                        account_number=self.account_number,
                                        report_platform_id=self.report_platform_id))
                self.next_state = ReportSlice.HOSTS_UPLOADED
                options = {'candidate_hosts': [], 'ready_to_archive': True}
                self.update_object_state(options=options)
            else:
                # need to not upload, but archive bc no hosts were valid
                LOG.info(format_message(self.prefix, 'There are no valid hosts to upload',
                                        account_number=self.account_number,
                                        report_platform_id=self.report_platform_id))
                self.next_state = ReportSlice.FAILED_VALIDATION
                options = {'ready_to_archive': True}
                self.update_object_state(options=options)
                self.archive_report_and_slices()
        except Exception as error:  # pylint: disable=broad-except
            LOG.error(format_message(self.prefix, 'The following error occurred: %s.' % str(error),
                                     account_number=self.account_number,
                                     report_platform_id=self.report_platform_id))
            self.determine_retry(ReportSlice.FAILED_HOSTS_UPLOAD, ReportSlice.VALIDATED,
                                 retry_type=ReportSlice.TIME)

    def generate_upload_candidates(self):
        """Generate dictionary of hosts that need to be uploaded to host inventory.

         If a retry has not occurred then we return the candidate_hosts
        but if a retry has occurred and failed at uploading, we want to retry
        the hosts that failed upload while excluding the ones that succeeded.
        """
        candidate_hosts = json.loads(self.report_or_slice.candidate_hosts)
        candidates = {}
        # we want to generate a dictionary of just the id mapped to the data
        # so we iterate the list creating a dictionary of the key: value if
        # the key is not 'cause' or 'status_code'
        candidates = {key: host[key] for host in candidate_hosts
                      for key in host.keys() if key not in ['cause', 'status_code']}
        return candidates

    # pylint:disable=too-many-locals
    @KAFKA_ERRORS.count_exceptions()  # noqa: C901 (too-complex)
    async def _upload_to_host_inventory_via_kafka(self, hosts):
        """
        Upload to the host inventory via kafka.

        :param: hosts <list> the hosts to upload.
        """
        self.prefix = 'UPLOAD TO INVENTORY VIA KAFKA'
        await self.producer.stop()
        self.producer = AIOKafkaProducer(
            loop=SLICE_PROCESSING_LOOP, bootstrap_servers=INSIGHTS_KAFKA_ADDRESS
        )
        try:
            await self.producer.start()
        except (KafkaConnectionError, TimeoutError):
            KAFKA_ERRORS.inc()
            self.should_run = False
            stop_all_event_loops()
            raise KafkaMsgHandlerError(
                format_message(
                    self.prefix,
                    'Unable to connect to kafka server. Closing producer.',
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id))
        total_hosts = len(hosts)
        count = 0
        send_futures = []
        associated_msg = []
        report = self.report_or_slice.report
        unique_id_base = '{}:{}:{}:'.format(report.request_id,
                                            report.report_platform_id,
                                            self.report_or_slice.report_slice_id)
        try:  # pylint: disable=too-many-nested-blocks
            for host_id, host in hosts.items():
                system_unique_id = unique_id_base + host_id
                count += 1
                upload_msg = {
                    'operation': 'add_host',
                    'data': host,
                    'platform_metadata': {'request_id': system_unique_id}
                }
                msg = bytes(json.dumps(upload_msg), 'utf-8')
                future = await self.producer.send(UPLOAD_TOPIC, msg)
                send_futures.append(future)
                associated_msg.append(upload_msg)
                if count % HOSTS_UPLOAD_FUTURES_COUNT == 0 or count == total_hosts:
                    LOG.info(
                        format_message(
                            self.prefix,
                            'Sending %s/%s hosts to the inventory service.' % (count, total_hosts),
                            account_number=self.account_number,
                            report_platform_id=self.report_platform_id))
                    try:
                        await asyncio.wait(send_futures, timeout=HOSTS_UPLOAD_TIMEOUT)
                        future_index = 0
                        for future_res in send_futures:
                            if future_res.exception():
                                LOG.error(
                                    'An exception occurred %s when trying to upload '
                                    'the following message: %s',
                                    future_res.exception(),
                                    associated_msg[future_index])
                            future_index += 1
                    except Exception as error:  # pylint: disable=broad-except
                        LOG.error('An exception occurred: %s', error)
                    send_futures = []
        except Exception as err:  # pylint: disable=broad-except
            LOG.error(format_message(
                self.prefix, 'The following error occurred: %s' % err))
            KAFKA_ERRORS.inc()
            self.should_run = False
            stop_all_event_loops()
            raise KafkaMsgHandlerError(
                format_message(
                    self.prefix,
                    'The following exception occurred: %s' % err,
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id))
        finally:
            await self.producer.stop()


def asyncio_report_processor_thread(loop):  # pragma: no cover
    """
    Worker thread function to run the asyncio event loop.

    Creates a report processor and calls the run method.

    :param loop: event loop
    :returns None
    """
    processor = ReportSliceProcessor()
    PROCESSOR_INSTANCES.append(processor)
    try:
        loop.run_until_complete(processor.run())
    except Exception:  # pylint: disable=broad-except
        pass


def initialize_report_slice_processor():  # pragma: no cover
    """
    Create asyncio tasks and daemon thread to run event loop.

    Calls the report processor thread.

    :param None
    :returns None
    """
    event_loop_thread = threading.Thread(target=asyncio_report_processor_thread,
                                         args=(SLICE_PROCESSING_LOOP,))
    event_loop_thread.daemon = True
    event_loop_thread.start()
