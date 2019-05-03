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
"""Report Processor."""

import asyncio
import json
import logging
import tarfile
import threading
from datetime import datetime
from http import HTTPStatus
from io import BytesIO

import pytz
import requests
from aiokafka import AIOKafkaProducer
from kafka.errors import ConnectionError as KafkaConnectionError
from processor.abstract_processor import (AbstractProcessor,
                                          FAILED_TO_DOWNLOAD, FAILED_TO_VALIDATE,
                                          INVALID_HOSTS, RETRY)
from processor.kafka_msg_handler import (KafkaMsgHandlerError,
                                         QPCReportException,
                                         format_message)

from api.models import (Report, ReportSlice, Status)
from config.settings.base import (INSIGHTS_KAFKA_ADDRESS,
                                  RETRIES_ALLOWED,
                                  RETRY_TIME)

LOG = logging.getLogger(__name__)
REPORT_PROCESSING_LOOP = asyncio.new_event_loop()
VALIDATION_TOPIC = 'platform.upload.validation'
SUCCESS_CONFIRM_STATUS = 'success'
FAILURE_CONFIRM_STATUS = 'failure'
RETRIES_ALLOWED = int(RETRIES_ALLOWED)
RETRY_TIME = int(RETRY_TIME)
HOSTS_PER_REQ = 1000
MAX_HOSTS_PER_REP = 10000


class FailDownloadException(Exception):
    """Use to report download errors that should not be retried."""

    pass


class RetryDownloadException(Exception):
    """Use to report download errors that should be retried."""

    pass


class FailExtractException(Exception):
    """Use to report extract errors that should not be retried."""

    pass


class RetryExtractException(Exception):
    """Use to report extract errors that should be retried."""

    pass


class ReportProcessor(AbstractProcessor):  # pylint: disable=too-many-instance-attributes
    """Class for processing report that have been created."""

    def __init__(self):
        """Create a report processor."""
        state_functions = {
            Report.NEW: self.transition_to_started,
            Report.STARTED: self.transition_to_downloaded,
            Report.DOWNLOADED: self.transition_to_validated,
            Report.VALIDATED: self.transition_to_validation_reported,
            Report.VALIDATION_REPORTED: self.archive_report_and_slices,
            Report.FAILED_DOWNLOAD: self.archive_report_and_slices,
            Report.FAILED_VALIDATION: self.archive_report_and_slices,
            Report.FAILED_VALIDATION_REPORTING: self.archive_report_and_slices}
        state_metrics = {
            Report.FAILED_DOWNLOAD: FAILED_TO_DOWNLOAD.inc,
            Report.FAILED_VALIDATION: FAILED_TO_VALIDATE.inc
        }
        self.async_states = [Report.VALIDATED]
        super().__init__(pre_delegate=self.pre_delegate,
                         state_functions=state_functions,
                         state_metrics=state_metrics,
                         async_states=self.async_states,
                         object_prefix='REPORT',
                         object_class=Report
                         )

    def pre_delegate(self):
        """Call the correct function based on report state.

        If the function is async, make sure to await it.
        """
        self.state = self.report_or_slice.state
        self.account_number = self.report_or_slice.rh_account
        self.upload_message = json.loads(self.report_or_slice.upload_srv_kafka_msg)
        if self.report_or_slice.report_platform_id:
            self.report_id = self.report_or_slice.report_platform_id
        if self.report_or_slice.upload_ack_status:
            self.status = self.report_or_slice.upload_ack_status

    def transition_to_downloaded(self):
        """Attempt to download report, extract json, and create slices.

        As long as we have one valid slice, we set the status to success.
        """
        self.prefix = 'ATTEMPTING DOWNLOAD'
        report_download_failed_msg = \
            'The report could not be downloaded due to the following error: %s.'
        LOG.info(format_message(
            self.prefix,
            'Attempting to download the report and extract the json. '
            'State is "%s".' % self.report_or_slice.state,
            account_number=self.account_number))
        try:
            report_tar_gz = self._download_report()
            metadata_json = self._extract_and_create_slices(report_tar_gz)
            report_platform_id = metadata_json.get('report_platform_id')
            report_version = metadata_json.get('report_version')
            qpc_server_version = metadata_json.get('qpc_server_version')
            qpc_server_id = metadata_json.get('qpc_server_id')
            self.next_state = Report.DOWNLOADED
            # update the report or slice with downloaded info
            self.update_object_state(report_id=report_platform_id,
                                     qpc_server_version=qpc_server_version,
                                     qpc_server_id=qpc_server_id,
                                     report_version=report_version)
            self.deduplicate_reports()
        except (FailDownloadException, FailExtractException) as err:
            LOG.error(format_message(
                self.prefix,
                report_download_failed_msg % err,
                account_number=self.account_number))
            self.next_state = Report.FAILED_DOWNLOAD
            self.update_object_state(ready_to_archive=True)
        except (RetryDownloadException, RetryExtractException) as err:
            LOG.error(format_message(
                self.prefix,
                report_download_failed_msg % err,
                account_number=self.account_number))
            self.determine_retry(Report.FAILED_DOWNLOAD, Report.STARTED)

    def transition_to_validated(self):
        """Validate that the report contents & move to validated state."""
        self.prefix = 'ATTEMPTING VALIDATE'
        LOG.info(format_message(
            self.prefix,
            'Validating the report contents. State is "%s".' % self.report_or_slice.state,
            account_number=self.account_number))
        # find all associated report slices
        report_slices = ReportSlice.objects.all().filter(report=self.report_or_slice)
        self.status = FAILURE_CONFIRM_STATUS
        for report_slice in report_slices:
            try:
                self.report_json = json.loads(report_slice.report_json)
                candidate_hosts, failed_hosts = self._validate_report_details()
                if candidate_hosts:
                    self.status = SUCCESS_CONFIRM_STATUS
                INVALID_HOSTS.set(len(failed_hosts))
                # Here we want to update the report state of the actual report slice
                self.update_slice_state(state=ReportSlice.NEW, report_slice=report_slice,
                                        candidate_hosts=candidate_hosts,
                                        failed_hosts=failed_hosts)
            except QPCReportException:
                # if any QPCReportExceptions occur, we know that the report is not valid
                # but has been successfully validated
                # that means that this slice is invalid and only awaits being archived
                self.update_slice_state(state=ReportSlice.FAILED_VALIDATION,
                                        report_slice=report_slice,
                                        ready_to_archive=True)
            except Exception as error:  # pylint: disable=broad-except
                # This slice blew up validation - we want to retry it later,
                # which means it enters our odd state of retrying validation
                LOG.error(format_message(self.prefix,
                                         'The following error occurred: %s.' % str(error)))
                self.update_slice_state(state=ReportSlice.RETRY_VALIDATION,
                                        report_slice=report_slice, retry=RETRY.increment)
        if self.status == 'failure':
            LOG.warning(
                format_message(
                    self.prefix,
                    'The uploaded report was invalid. Status set to "%s".' % self.status,
                    account_number=self.account_number))
        self.next_state = Report.VALIDATED
        self.update_object_state(status=self.status)

    async def transition_to_validation_reported(self):
        """Upload the validation status & move to validation reported state."""
        self.prefix = 'ATTEMPTING STATUS UPLOAD'
        LOG.info(format_message(
            self.prefix,
            'Uploading validation status "%s". State is "%s".' %
            (self.status, self.state),
            account_number=self.account_number, report_id=self.report_id))
        message_hash = self.upload_message['hash']
        try:
            await self._send_confirmation(message_hash)
            self.next_state = Report.VALIDATION_REPORTED
            self.update_object_state(ready_to_archive=True)
            LOG.info(format_message(
                self.prefix,
                'Status successfully uploaded.',
                account_number=self.account_number, report_id=self.report_id))
            if self.status == FAILURE_CONFIRM_STATUS:
                self.update_object_state(retry=RETRY.keep_same, ready_to_archive=True)
                self.archive_report_and_slices()
        except Exception as error:  # pylint: disable=broad-except
            LOG.error(format_message(self.prefix, 'The following error occurred: %s.' % str(error),
                                     account_number=self.account_number, report_id=self.report_id))
            self.determine_retry(Report.FAILED_VALIDATION_REPORTING, Report.VALIDATED)

    def create_report_slice(self, report_json, report_slice_id):
        """Create report slice.

        Returns a boolean regarding whether or not the slice was created.
        """
        LOG.info(
            format_message(
                self.prefix, 'Creating report slice %s' % report_slice_id,
                account_number=self.account_number, report_id=self.report_id))

        # first we should see if any slices exist with this slice id & report_platform_id
        # if they exist we will not create the slice
        created = False
        existing_report_slices = ReportSlice.objects.filter(
            report_platform_id=self.report_id).filter(report_slice_id=report_slice_id)
        if existing_report_slices.count() > 0:
            LOG.error(format_message(
                self.prefix,
                'a report slice with the report_platform_id %s and report_slice_id %s '
                'already exists.' % (self.report_id, report_slice_id),
                account_number=self.account_number, report_id=self.report_id))
            return created

        # The slice does not exist, so we should create it
        report_slice = ReportSlice(
            state=ReportSlice.PENDING,
            rh_account=self.account_number,
            state_info=json.dumps([ReportSlice.PENDING]),
            last_update_time=datetime.now(pytz.utc),
            retry_count=0,
            report_json=json.dumps(report_json),
            report_platform_id=self.report_id,
            failed_hosts=json.dumps({}),
            candidate_hosts=json.dumps({}),
            report_slice_id=report_slice_id,
            report=self.report_or_slice
        )
        report_slice.save()
        LOG.info(
            format_message(
                self.prefix, 'Successfully created report slice %s' % report_slice_id,
                account_number=self.account_number, report_id=self.report_id))
        created = True
        return created, report_slice

    # pylint: disable=too-many-arguments
    def update_slice_state(self, state, report_slice, retry=RETRY.clear,
                           retry_type=None, candidate_hosts=None,   # noqa: C901 (too-complex)
                           failed_hosts=None, ready_to_archive=None):
        """
        Update the report processor state and save.

        :param retry: <enum> Retry.clear=clear count, RETRY.increment=increase count
        :param retry_type: <str> either time=retry after time,
            git_commit=retry after new commit
        :param report_json: <dict> dictionary containing the report json
        :param report_id: <str> string containing report_platform_id
        :param candidate_hosts: <dict> dictionary containing hosts that were
            successfully verified and uploaded
        :param failed_hosts: <dict> dictionary containing hosts that failed
            verification or upload
        :param status: <str> either success or failure based on the report
        """
        try:
            status_info = Status()
            report_slice.last_update_time = datetime.now(pytz.utc)
            report_slice.state = state
            report_slice.git_commit = status_info.git_commit
            if not retry_type:
                retry_type = ReportSlice.TIME
            if retry == RETRY.clear:
                # reset the count to 0 (default behavior)
                report_slice.retry_count = 0
                report_slice.retry_type = ReportSlice.TIME
            elif retry == RETRY.increment:
                report_slice.retry_count += 1
                report_slice.retry_type = retry_type
            # the other choice for retry is RETRY.keep_same in which case we don't
            # want to do anything to the retry count bc we want to preserve as is
            if candidate_hosts is not None:
                # candidate_hosts will get smaller and smaller until it hopefully
                # is empty because we have taken care of all ofthe candidates so
                # we rewrite this each time
                report_slice.candidate_hosts = json.dumps(candidate_hosts)
            if failed_hosts:
                # for failed hosts this list can keep growing, so we add the
                # newly failed hosts to the previous value
                failed = json.loads(report_slice.failed_hosts)
                for host in failed_hosts:
                    failed.append(host)
                report_slice.failed_hosts = json.dumps(failed)
            if ready_to_archive:
                report_slice.ready_to_archive = ready_to_archive
            state_info = json.loads(report_slice.state_info)
            state_info.append(self.next_state)
            report_slice.state_info = json.dumps(state_info)
            report_slice.save()
            LOG.info(
                format_message(
                    self.prefix,
                    'Successfully updated report slice %s' % report_slice.report_slice_id,
                    account_number=self.account_number, report_id=self.report_id))
        except Exception as error:  # pylint: disable=broad-except
            LOG.error(format_message(
                self.prefix,
                'Could not update report slice record due to the following error %s.' % str(error),
                account_number=self.account_number, report_id=self.report_id))

    def _download_report(self):
        """
        Download report.

        :returns content: The tar.gz binary content or None if there are errors.
        """
        self.prefix = 'REPORT DOWNLOAD'
        try:
            report_url = self.upload_message.get('url', None)
            if not report_url:
                raise FailDownloadException(
                    format_message(
                        self.prefix,
                        'kafka message missing report url.  Message: %s' % self.upload_message,
                        account_number=self.account_number))

            LOG.info(format_message(
                self.prefix,
                'downloading %s' % report_url, account_number=self.account_number))
            download_response = requests.get(report_url)
            if download_response.status_code != HTTPStatus.OK:
                raise RetryDownloadException(
                    format_message(self.prefix,
                                   'HTTP status code %s returned for URL %s.  Message: %s' % (
                                       download_response.status_code,
                                       report_url,
                                       self.upload_message),
                                   account_number=self.account_number))

            LOG.info(format_message(
                self.prefix,
                'successfully downloaded TAR %s' % report_url,
                account_number=self.account_number
            ))
            return download_response.content
        except FailDownloadException as fail_err:
            raise fail_err

        except requests.exceptions.HTTPError as err:
            raise RetryDownloadException(
                format_message(self.prefix,
                               'Unexpected http error for URL %s. Error: %s' % (
                                   report_url,
                                   err),
                               account_number=self.account_number))

        except Exception as err:
            raise RetryDownloadException(
                format_message(self.prefix,
                               'Unexpected error for URL %s. Error: %s' %
                               (report_url, err),
                               account_number=self.account_number))

    # pylint: disable=too-many-locals, too-many-nested-blocks, too-many-branches
    def _extract_and_create_slices(self, report_tar_gz):  # noqa: C901 (too-complex)
        """Extract Insights report from tar.gz file.

        :param report_tar_gz: A hexstring or BytesIO tarball
            saved in memory with gzip compression.
        :returns: Insights report as dict
        """
        self.prefix = 'EXTRACT REPORT FROM TAR'
        try:
            tar = tarfile.open(fileobj=BytesIO(report_tar_gz), mode='r:gz')
            files = tar.getmembers()
            json_files = []
            metadata_file = None
            for file in files:
                # First we need to Find the metadata file
                if 'metadata.json' in file.name:
                    metadata_file = tar.extractfile(file)
                # Next we want to add all .json files to our list
                elif '.json' in file.name:
                    json_files.append(file)
            if json_files and metadata_file:
                try:
                    metadata_str = metadata_file.read().decode('utf-8')
                    metadata_json = json.loads(metadata_str)
                    # save all of the metadata info to the report record
                    report_slices = metadata_json.get('report_slices', {})
                    self.report_id = metadata_json.get('report_platform_id')
                    report_names = {}
                    # loop through the keys in the report_slices dictionary to find the names of the
                    # files that we need to save
                    # check the number of hosts and if permissible, find the associated json payload
                    for report_name, report_info in report_slices.items():
                        num_hosts = int(report_info.get('number_hosts', MAX_HOSTS_PER_REP + 1))
                        if num_hosts <= MAX_HOSTS_PER_REP:
                            # loop through the list of json files found within the payload
                            for file in json_files:
                                if report_name in file.name:
                                    report_slice = tar.extractfile(file)
                                    report_slice_string = report_slice.read().decode('utf-8')
                                    report_slice_json = json.loads(report_slice_string)
                                    report_slice_id = report_slice_json.get('report_slice_id', '')
                                    created = self.create_report_slice(
                                        report_json=report_slice_json,
                                        report_slice_id=report_slice_id)
                                    if created:
                                        report_names[report_name] = True
                                    break
                        else:
                            # else we want to warn that the report had too many hosts for yupana
                            # to process
                            large_slice_message = 'Report %s has %s hosts. '\
                                                  'There must be no more than %s hosts per'\
                                                  ' report.' % \
                                                  (report_name, str(num_hosts),
                                                   str(MAX_HOSTS_PER_REP))
                            LOG.warning(
                                format_message(self.prefix, large_slice_message,
                                               account_number=self.account_number,
                                               report_id=self.report_id))

                    if not report_names:
                        raise FailExtractException(format_message(
                            self.prefix,
                            'Report contained no valid JSON payloads.',
                            account_number=self.account_number))
                    LOG.info(
                        format_message(
                            self.prefix,
                            'successfully extracted & created report slices',
                            account_number=self.account_number,
                            report_id=self.report_id))
                    return metadata_json
                except ValueError as error:
                    raise FailExtractException(
                        format_message(self.prefix,
                                       'Report not JSON. Error: %s' % str(error),
                                       account_number=self.account_number))
            raise FailExtractException(
                format_message(self.prefix,
                               'Tar does not contain JSON metadata & report files.',
                               account_number=self.account_number))
        except FailExtractException as qpc_err:
            raise qpc_err
        except tarfile.ReadError as err:
            raise FailExtractException(format_message(
                self.prefix,
                'Unexpected error reading tar.gz: %s' % str(err),
                account_number=self.account_number))
        except Exception as err:
            raise RetryExtractException(
                format_message(self.prefix,
                               'Unexpected error reading tar.gz: %s' % str(err),
                               account_number=self.account_number))

    async def _send_confirmation(self, file_hash):  # pragma: no cover
        """
        Send kafka validation message to Insights Upload service.

        When a new file lands for topic 'qpc' we must validate it
        so that it will be made permanently available to other
        apps listening on the 'available' topic.
        :param: file_hash (String): Hash for file being confirmed.
        :returns None
        """
        self.prefix = 'REPORT VALIDATION STATE ON KAFKA'
        producer = AIOKafkaProducer(
            loop=REPORT_PROCESSING_LOOP, bootstrap_servers=INSIGHTS_KAFKA_ADDRESS
        )
        try:
            await producer.start()
        except (KafkaConnectionError, TimeoutError):
            await producer.stop()
            raise KafkaMsgHandlerError(
                format_message(
                    self.prefix,
                    'Unable to connect to kafka server.  Closing producer.',
                    account_number=self.account_number,
                    report_id=self.report_id))
        try:
            validation = {
                'hash': file_hash,
                'validation': self.status
            }
            msg = bytes(json.dumps(validation), 'utf-8')
            await producer.send_and_wait(VALIDATION_TOPIC, msg)
            LOG.info(
                format_message(
                    self.prefix,
                    'Send %s validation status to file upload on kafka' % self.status,
                    account_number=self.account_number,
                    report_id=self.report_id))
        finally:
            await producer.stop()

    def deduplicate_reports(self):
        """If a report with the same id already exists, archive the new report."""
        try:
            existing_reports = Report.objects.filter(
                report_platform_id=self.report_id)
            if existing_reports.count() > 1:
                LOG.error(format_message(
                    self.prefix,
                    'a report with the report_platform_id %s already exists.' %
                    self.report_or_slice.report_platform_id,
                    account_number=self.account_number, report_id=self.report_id))
                self.archive_report_and_slices()
        except Report.DoesNotExist:
            pass


def asyncio_report_processor_thread(loop):  # pragma: no cover
    """
    Worker thread function to run the asyncio event loop.

    Creates a report processor and calls the run method.

    :param loop: event loop
    :returns None
    """
    processor = ReportProcessor()
    loop.run_until_complete(processor.run())


def initialize_report_processor():  # pragma: no cover
    """
    Create asyncio tasks and daemon thread to run event loop.

    Calls the report processor thread.

    :param None
    :returns None
    """
    event_loop_thread = threading.Thread(target=asyncio_report_processor_thread,
                                         args=(REPORT_PROCESSING_LOOP,))
    event_loop_thread.daemon = True
    event_loop_thread.start()
