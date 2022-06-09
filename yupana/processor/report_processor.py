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
from django.db import transaction
from kafka.errors import KafkaConnectionError
from processor.abstract_processor import (
    AbstractProcessor,
    FAILED_TO_DOWNLOAD, FAILED_TO_VALIDATE, RETRY)
from processor.processor_utils import (PROCESSOR_INSTANCES,
                                       REPORT_PROCESSING_LOOP,
                                       format_message,
                                       print_error_loop_event)
from processor.report_consumer import (DB_ERRORS,
                                       KAFKA_ERRORS,
                                       KafkaMsgHandlerError,
                                       QPCReportException)
from prometheus_client import Counter, Gauge

from api.models import (Report, ReportSlice, Status)
from api.serializers import ReportSerializer, ReportSliceSerializer
from config.settings.base import (INSIGHTS_KAFKA_ADDRESS,
                                  MAX_HOSTS_PER_REP,
                                  RETRIES_ALLOWED,
                                  RETRY_TIME,
                                  VALIDATION_TOPIC)

LOG = logging.getLogger(__name__)
SUCCESS_CONFIRM_STATUS = 'success'
FAILURE_CONFIRM_STATUS = 'failure'
RETRIES_ALLOWED = int(RETRIES_ALLOWED)
RETRY_TIME = int(RETRY_TIME)
MAX_HOSTS_PER_REP = int(MAX_HOSTS_PER_REP)
HOSTS_PER_REPORT_SATELLITE = Gauge('hosts_per_sat_rep',
                                   'Hosts count in a satellite report',
                                   ['account_number'])
HOSTS_PER_REPORT_QPC = Gauge('hosts_per_qpc_rep',
                             'Hosts count in a QPC report',
                             ['account_number'])
HOSTS_COUNTER = Counter('yupana_hosts_count',
                        'Total number of hosts uploaded',
                        ['account_number', 'source'])
PROCESSOR_NAME = 'report_processor'


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
        self.processor_name = PROCESSOR_NAME
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
            Report.FAILED_DOWNLOAD: FAILED_TO_DOWNLOAD,
            Report.FAILED_VALIDATION: FAILED_TO_VALIDATE
        }
        self.async_states = [Report.VALIDATED]
        self.producer = AIOKafkaProducer(
            loop=REPORT_PROCESSING_LOOP, bootstrap_servers=INSIGHTS_KAFKA_ADDRESS
        )
        super().__init__(pre_delegate=self.pre_delegate,
                         state_functions=state_functions,
                         state_metrics=state_metrics,
                         async_states=self.async_states,
                         object_prefix='REPORT',
                         object_class=Report,
                         object_serializer=ReportSerializer
                         )

    def pre_delegate(self):
        """Call the correct function based on report state.

        If the function is async, make sure to await it.
        """
        self.state = self.report_or_slice.state
        self.account_number = self.report_or_slice.account
        self.org_id = self.report_or_slice.org_id
        self.upload_message = json.loads(self.report_or_slice.upload_srv_kafka_msg)
        if self.report_or_slice.report_platform_id:
            self.report_platform_id = self.report_or_slice.report_platform_id
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
            report_tar = self._download_report()
            options = self._extract_and_create_slices(report_tar)
            self.next_state = Report.DOWNLOADED
            # update the report or slice with downloaded info
            self.update_object_state(options=options)
            self.deduplicate_reports()
        except (FailDownloadException, FailExtractException) as err:
            LOG.error(format_message(
                self.prefix,
                report_download_failed_msg % err,
                account_number=self.account_number))
            self.next_state = Report.FAILED_DOWNLOAD
            options = {'ready_to_archive': True}
            self.update_object_state(options=options)
        except (RetryDownloadException, RetryExtractException) as err:
            LOG.error(format_message(
                self.prefix,
                report_download_failed_msg % err,
                account_number=self.account_number))
            self.determine_retry(Report.FAILED_DOWNLOAD, Report.STARTED)

    @DB_ERRORS.count_exceptions()
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
                candidate_hosts = self._validate_report_details()
                if candidate_hosts:
                    self.status = SUCCESS_CONFIRM_STATUS
                # Here we want to update the report state of the actual report slice
                options = {'state': ReportSlice.NEW,
                           'candidate_hosts': candidate_hosts}
                self.update_slice_state(options=options, report_slice=report_slice)
            except QPCReportException:
                # if any QPCReportExceptions occur, we know that the report is not valid
                # but has been successfully validated
                # that means that this slice is invalid and only awaits being archived
                options = {'state': ReportSlice.FAILED_VALIDATION, 'ready_to_archive': True}
                self.update_slice_state(options=options, report_slice=report_slice)
            except Exception as error:  # pylint: disable=broad-except
                # This slice blew up validation - we want to retry it later,
                # which means it enters our odd state of retrying validation
                LOG.error(format_message(self.prefix,
                                         'The following error occurred: %s.' % str(error)))
                options = {'state': ReportSlice.RETRY_VALIDATION,
                           'retry': RETRY.increment}
                self.update_slice_state(options=options, report_slice=report_slice)
        if self.status == 'failure':
            LOG.warning(
                format_message(
                    self.prefix,
                    'The uploaded report was invalid. Status set to "%s".' % self.status,
                    account_number=self.account_number))
        self.next_state = Report.VALIDATED
        options = {'status': self.status}
        self.update_object_state(options=options)

    async def transition_to_validation_reported(self):
        """Upload the validation status & move to validation reported state."""
        self.prefix = 'ATTEMPTING STATUS UPLOAD'
        LOG.info(format_message(
            self.prefix,
            'Uploading validation status "%s". State is "%s".' %
            (self.status, self.state),
            account_number=self.account_number, report_platform_id=self.report_platform_id))
        try:
            message_hash = self.upload_message['request_id']
            await self._send_confirmation(message_hash)
            self.next_state = Report.VALIDATION_REPORTED
            options = {'ready_to_archive': True}
            self.update_object_state(options=options)
            LOG.info(format_message(
                self.prefix,
                'Status successfully uploaded.',
                account_number=self.account_number, report_platform_id=self.report_platform_id))
            if self.status == FAILURE_CONFIRM_STATUS:
                options = {'retry': RETRY.keep_same, 'ready_to_archive': True}
                self.update_object_state(options=options)
                self.archive_report_and_slices()
        except Exception as error:  # pylint: disable=broad-except
            LOG.error(format_message(
                self.prefix, 'The following error occurred: %s.' % str(error),
                account_number=self.account_number,
                report_platform_id=self.report_platform_id))
            self.determine_retry(Report.FAILED_VALIDATION_REPORTING, Report.VALIDATED)

    @DB_ERRORS.count_exceptions()
    def create_report_slice(self, options):
        """Create report slice.

        :param report_json: <dict> the report info in json format
        :param report_slice_id: <str> the report slice id
        :param hosts_count: <int> the number of hosts inside the report slice
        :returns boolean regarding whether or not the slice was created.
        """
        report_json = options.get('report_json')
        report_slice_id = options.get('report_slice_id')
        hosts_count = options.get('hosts_count')
        source = options.get('source')
        source_metadata = options.get('source_metadata')
        LOG.info(
            format_message(
                self.prefix, 'Creating report slice %s' % report_slice_id,
                account_number=self.account_number, report_platform_id=self.report_platform_id))

        # first we should see if any slices exist with this slice id & report_platform_id
        # if they exist we will not create the slice
        created = False
        existing_report_slices = ReportSlice.objects.filter(
            report_platform_id=self.report_platform_id).filter(report_slice_id=report_slice_id)
        if existing_report_slices.count() > 0:
            LOG.error(format_message(
                self.prefix,
                'a report slice with the report_platform_id %s and report_slice_id %s '
                'already exists.' % (self.report_platform_id, report_slice_id),
                account_number=self.account_number, report_platform_id=self.report_platform_id))
            return created

        report_slice = {
            'state': ReportSlice.PENDING,
            'account': self.account_number,
            'org_id': self.org_id,
            'state_info': json.dumps([ReportSlice.PENDING]),
            'last_update_time': datetime.now(pytz.utc),
            'retry_count': 0,
            'report_json': json.dumps(report_json),
            'report_platform_id': self.report_platform_id,
            'failed_hosts': json.dumps({}),
            'candidate_hosts': json.dumps({}),
            'report_slice_id': report_slice_id,
            'report': self.report_or_slice.id,
            'hosts_count': hosts_count,
            'source': source,
            'source_metadata': json.dumps(source_metadata),
            'creation_time': datetime.now(pytz.utc)
        }
        slice_serializer = ReportSliceSerializer(data=report_slice)
        if slice_serializer.is_valid(raise_exception=True):
            slice_serializer.save()
            LOG.info(
                format_message(
                    self.prefix,
                    'Successfully created report slice %s' % report_slice_id,
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id))
        return True

    # pylint: disable=too-many-arguments,too-many-locals
    def update_slice_state(self, options, report_slice):  # noqa: C901 (too-complex)
        """
        Update the report processor state and save.

        :param options: <dict> dictionary potentially containing the following:
            report_slice: <ReportSlice> the report slice to update
            state: <str> the state to update to
            retry: <enum> Retry.clear=clear count, RETRY.increment=increase count
            retry_type: <str> either time=retry after time,
                git_commit=retry after new commit
            report_json: <dict> dictionary containing the report json
            report_platform_id: <str> string containing report_platform_id
            candidate_hosts: <dict> dictionary containing hosts that were
                successfully verified and uploaded
            failed_hosts: <dict> dictionary containing hosts that failed
                verification or upload
            ready_to_archive: <bool> boolean on whether or not to archive
        """
        try:
            state = options.get('state')
            retry_type = options.get('retry_type')
            retry = options.get('retry', RETRY.clear)
            candidate_hosts = options.get('candidate_hosts')
            failed_hosts = options.get('failed_hosts')
            ready_to_archive = options.get('ready_to_archive')
            status_info = Status()
            report_slice.last_update_time = datetime.now(pytz.utc)
            report_slice.state = state
            report_slice.git_commit = status_info.git_commit
            report_slice_data = {
                'last_update_time': datetime.now(pytz.utc),
                'state': state,
                'git_commit': status_info.git_commit
            }
            if not retry_type:
                retry_type = ReportSlice.TIME
            if retry == RETRY.clear:
                # After a successful transaction when we have reached the update
                # point, we want to set the retry count back to 0 because
                # any future failures should be unrelated
                report_slice_data['retry_count'] = 0
                report_slice_data['retry_type'] = ReportSlice.TIME
            elif retry == RETRY.increment:
                current_count = report_slice.retry_count
                report_slice_data['retry_count'] = current_count + 1
                report_slice_data['retry_type'] = ReportSlice.TIME
            # the other choice for retry is RETRY.keep_same in which case we don't
            # want to do anything to the retry count bc we want to preserve as is
            if candidate_hosts is not None:
                # candidate_hosts will get smaller and smaller until it hopefully
                # is empty because we have taken care of all ofthe candidates so
                # we rewrite this each time
                report_slice_data['candidate_hosts'] = json.dumps(candidate_hosts)
            if failed_hosts:
                # for failed hosts this list can keep growing, so we add the
                # newly failed hosts to the previous value
                failed = json.loads(report_slice.failed_hosts)
                for host in failed_hosts:
                    failed.append(host)
                report_slice_data['failed_hosts'] = json.dumps(failed)
            if ready_to_archive:
                report_slice_data['ready_to_archive'] = ready_to_archive
            state_info = json.loads(report_slice.state_info)
            state_info.append(state)
            report_slice_data['state_info'] = json.dumps(state_info)
            serializer = ReportSliceSerializer(
                instance=report_slice,
                data=report_slice_data,
                partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            LOG.info(
                format_message(
                    self.prefix,
                    'Successfully updated report slice %s' % report_slice.report_slice_id,
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id))
        except Exception as error:  # pylint: disable=broad-except
            DB_ERRORS.inc()
            self.should_run = False
            LOG.error(format_message(
                self.prefix,
                'Could not update report slice record due to the following error %s.' % str(error),
                account_number=self.account_number, report_platform_id=self.report_platform_id))
            print_error_loop_event()

    def _download_report(self):
        """
        Download report.

        :returns content: The tar binary content or None if there are errors.
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

    def validate_metadata_file(self, tar, metadata):   # noqa: C901 (too-complex)
        """Validate the contents of the metadata file.

        :param tar: the tarfile object.
        :param metadata: metadata file object.
        :returns: report_slice_ids
        """
        LOG.info(format_message(self.prefix,
                                'Attempting to decode the file %s' % metadata.name,
                                account_number=self.account_number,
                                report_platform_id=self.report_platform_id))
        metadata_file = tar.extractfile(metadata)
        try:
            metadata_str = metadata_file.read().decode('utf-8')
        except UnicodeDecodeError as error:
            decode_error_message = 'Attempting to decode the file'\
                ' %s resulted in the following error: %s. Discarding file.' % \
                (metadata_file.name, error)
            LOG.exception(
                format_message(self.prefix, decode_error_message,
                               account_number=self.account_number,
                               report_platform_id=self.report_platform_id)
            )
            return {}
        LOG.info(format_message(self.prefix,
                                'Successfully decoded the file %s' % metadata.name,
                                account_number=self.account_number,
                                report_platform_id=self.report_platform_id))
        metadata_json = json.loads(metadata_str)
        required_keys = ['report_id', 'host_inventory_api_version',
                         'source', 'report_slices']
        missing_keys = []
        for key in required_keys:
            required_key = metadata_json.get(key)
            if not required_key:
                missing_keys.append(key)

        if missing_keys:
            missing_keys_str = ', '.join(missing_keys)
            raise FailExtractException(
                format_message(
                    self.prefix,
                    'Metadata is missing required fields: %s.' % missing_keys_str,
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id))

        self.report_platform_id = metadata_json.get('report_id')
        host_inventory_api_version = metadata_json.get('host_inventory_api_version')
        source = metadata_json.get('source', '')
        # we should save the above information into the report object
        options = {
            'report_platform_id': self.report_platform_id,
            'host_inventory_api_version': host_inventory_api_version,
            'source': source
        }

        source_metadata = metadata_json.get('source_metadata')
        # if source_metadata exists, we should log it
        if source_metadata:
            LOG.info(format_message(
                self.prefix,
                'The following source metadata was uploaded: %s' % source_metadata,
                account_number=self.account_number,
                report_platform_id=self.report_platform_id
            ))
            options['source_metadata'] = source_metadata
        self.update_object_state(options)
        invalid_slice_ids = {}
        valid_slice_ids = {}
        report_slices = metadata_json.get('report_slices', {})
        # we need to verify that the report slices have the appropriate number of hosts
        total_hosts_in_report = 0
        for report_slice_id, report_info in report_slices.items():
            num_hosts = int(report_info.get('number_hosts', MAX_HOSTS_PER_REP + 1))
            if num_hosts <= MAX_HOSTS_PER_REP:
                total_hosts_in_report += num_hosts
                valid_slice_ids[report_slice_id] = num_hosts
            else:
                invalid_slice_ids[report_slice_id] = num_hosts
        # record how many hosts there were for grafana charts
        HOSTS_COUNTER.labels(
            account_number=self.account_number,
            source=source).inc(total_hosts_in_report)
        if source.lower() == 'qpc':
            HOSTS_PER_REPORT_QPC.labels(
                account_number=self.account_number).set(total_hosts_in_report)
        elif source.lower() == 'satellite':
            HOSTS_PER_REPORT_SATELLITE.labels(
                account_number=self.account_number).set(total_hosts_in_report)
        # if any reports were over the max number of hosts, we need to log
        if invalid_slice_ids:
            for report_slice_id, num_hosts in invalid_slice_ids.items():
                large_slice_message = 'Report %s has %s hosts. '\
                    'There must be no more than %s hosts per'\
                    ' report.' % \
                    (report_slice_id, str(num_hosts),
                     str(MAX_HOSTS_PER_REP))
                LOG.warning(
                    format_message(self.prefix, large_slice_message,
                                   account_number=self.account_number,
                                   report_platform_id=self.report_platform_id))

        return valid_slice_ids, options

    # pylint: disable=too-many-branches, too-many-statements
    def _extract_and_create_slices(self, report_tar):  # noqa: C901 (too-complex)
        """Extract Insights report from tar file.

        :param report_tar: A hexstring or BytesIO tarball
            saved in memory with gzip|bz2|lzma compression.
        :returns: Insights report as dict
        """
        self.prefix = 'EXTRACT REPORT FROM TAR'
        try:  # pylint: disable=too-many-nested-blocks
            tar = tarfile.open(fileobj=BytesIO(report_tar), mode='r:*')
            files = tar.getmembers()
            json_files = []
            metadata_file = None
            for file in files:
                # First we need to Find the metadata file
                if '/metadata.json' in file.name or file.name == 'metadata.json':
                    metadata_file = file
                # Next we want to add all .json files to our list
                elif '.json' in file.name:
                    json_files.append(file)
            if json_files and metadata_file:
                try:
                    valid_slice_ids, options = self.validate_metadata_file(tar, metadata_file)
                    report_names = []
                    for report_id, num_hosts in valid_slice_ids.items():
                        for file in json_files:
                            if report_id in file.name:
                                matches_metadata = True
                                mismatch_message = ''
                                report_slice = tar.extractfile(file)
                                LOG.info(format_message(
                                    self.prefix,
                                    'Attempting to decode the file %s' % file.name,
                                    account_number=self.account_number,
                                    report_platform_id=self.report_platform_id))
                                try:
                                    report_slice_string = report_slice.read().decode('utf-8')
                                except UnicodeDecodeError as error:
                                    decode_error_message = 'Attempting to decode the file'\
                                        ' %s resulted in the following error: %s. '\
                                        'Discarding file.' % (file.name, error)
                                    LOG.exception(
                                        format_message(self.prefix, decode_error_message,
                                                       account_number=self.account_number,
                                                       report_platform_id=self.report_platform_id)
                                    )
                                    continue
                                LOG.info(format_message(
                                    self.prefix,
                                    'Successfully decoded the file %s' % file.name,
                                    account_number=self.account_number,
                                    report_platform_id=self.report_platform_id))
                                report_slice_json = json.loads(report_slice_string)
                                report_slice_id = report_slice_json.get('report_slice_id', '')
                                if report_slice_id != report_id:
                                    matches_metadata = False
                                    invalid_report_id = 'Metadata & filename reported the '\
                                        '"report_slice_id" as %s but the "report_slice_id" '\
                                        'inside the JSON has a value of %s. ' % \
                                        (report_id, report_slice_id)
                                    mismatch_message += invalid_report_id
                                hosts = report_slice_json.get('hosts', {})
                                if len(hosts) != num_hosts:
                                    matches_metadata = False
                                    invalid_hosts = 'Metadata for report slice'\
                                        ' %s reported %d hosts '\
                                        'but report contains %d hosts. ' % \
                                        (report_slice_id, num_hosts, len(hosts))
                                    mismatch_message += invalid_hosts
                                if not matches_metadata:
                                    mismatch_message += 'Metadata must match report slice data. '\
                                        'Discarding the report slice as invalid. '
                                    LOG.warning(
                                        format_message(self.prefix, mismatch_message,
                                                       account_number=self.account_number,
                                                       report_platform_id=self.report_platform_id))
                                    continue
                                slice_options = {
                                    'report_json': report_slice_json,
                                    'report_slice_id': report_slice_id,
                                    'hosts_count': num_hosts,
                                    'source': options.get('source'),
                                    'source_metadata': options.get('source_metadata', {})
                                }
                                created = self.create_report_slice(
                                    slice_options)
                                if created:
                                    report_names.append(report_id)

                    if not report_names:
                        raise FailExtractException(format_message(
                            self.prefix,
                            'Report contained no valid report slices.',
                            account_number=self.account_number))
                    LOG.info(
                        format_message(
                            self.prefix,
                            'successfully extracted & created report slices',
                            account_number=self.account_number,
                            report_platform_id=self.report_platform_id))
                    return options

                except ValueError as error:
                    raise FailExtractException(
                        format_message(self.prefix,
                                       'Report is not valid JSON. Error: %s' % str(error),
                                       account_number=self.account_number))
            raise FailExtractException(
                format_message(self.prefix,
                               'Tar does not contain valid JSON metadata & report files.',
                               account_number=self.account_number))
        except FailExtractException as qpc_err:
            raise qpc_err
        except tarfile.ReadError as err:
            raise FailExtractException(format_message(
                self.prefix,
                'Unexpected error reading tar file: %s' % str(err),
                account_number=self.account_number))
        except Exception as err:
            raise RetryExtractException(
                format_message(self.prefix,
                               'Unexpected error reading tar file: %s' % str(err),
                               account_number=self.account_number))

    @KAFKA_ERRORS.count_exceptions()
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
        await self.producer.stop()
        self.producer = AIOKafkaProducer(
            loop=REPORT_PROCESSING_LOOP, bootstrap_servers=INSIGHTS_KAFKA_ADDRESS
        )
        try:
            await self.producer.start()
        except (KafkaConnectionError, TimeoutError, Exception):
            KAFKA_ERRORS.inc()
            self.should_run = False
            await self.producer.stop()
            print_error_loop_event()
            raise KafkaMsgHandlerError(
                format_message(
                    self.prefix,
                    'Unable to connect to kafka server.',
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id))
        try:
            validation = {
                'hash': file_hash,
                'request_id': self.report_or_slice.request_id,
                'validation': self.status
            }
            msg = bytes(json.dumps(validation), 'utf-8')
            await self.producer.send_and_wait(VALIDATION_TOPIC, msg)
            LOG.info(
                format_message(
                    self.prefix,
                    'Send %s validation status to file upload on kafka' % self.status,
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id))
        except Exception as err:  # pylint: disable=broad-except
            KAFKA_ERRORS.inc()
            LOG.error(format_message(
                self.prefix, 'The following error occurred: %s' % err))
            print_error_loop_event()

        finally:
            await self.producer.stop()

    @DB_ERRORS.count_exceptions()
    @transaction.atomic
    def deduplicate_reports(self):
        """If a report with the same id already exists, archive the new report."""
        try:
            existing_reports = Report.objects.filter(
                report_platform_id=self.report_platform_id)
            if existing_reports.count() > 1:
                LOG.error(format_message(
                    self.prefix,
                    'a report with the report_platform_id %s already exists.' %
                    self.report_or_slice.report_platform_id,
                    account_number=self.account_number, report_platform_id=self.report_platform_id))
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
    PROCESSOR_INSTANCES.append(processor)
    try:
        loop.run_until_complete(processor.run())
    except Exception:  # pylint: disable=broad-except
        pass


def initialize_report_processor():  # pragma: no cover
    """
    Create asyncio tasks and daemon thread to run event loop.

    Calls the report processor thread.

    :param None
    :returns None
    """
    event_loop_thread = threading.Thread(target=asyncio_report_processor_thread,
                                         name=PROCESSOR_NAME,
                                         args=(REPORT_PROCESSING_LOOP,))
    event_loop_thread.daemon = True
    event_loop_thread.start()
