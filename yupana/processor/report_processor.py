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
"""Upload message report processor."""

import asyncio
import base64
import json
import logging
import math
import tarfile
import threading
from datetime import datetime
from enum import Enum
from http import HTTPStatus
from io import BytesIO

import pytz
import requests
from aiokafka import AIOKafkaProducer
from django.db import transaction
from kafka.errors import ConnectionError as KafkaConnectionError
from processor.kafka_msg_handler import (KafkaMsgHandlerError,
                                         QPCReportException,
                                         format_message)

from api.models import Report, ReportArchive, Status
from config.settings.base import (INSIGHTS_HOST_INVENTORY_URL,
                                  INSIGHTS_KAFKA_ADDRESS,
                                  RETRIES_ALLOWED,
                                  RETRY_TIME)

LOG = logging.getLogger(__name__)
PROCESSING_LOOP = asyncio.new_event_loop()
VALIDATION_TOPIC = 'platform.upload.validation'
SUCCESS_CONFIRM_STATUS = 'success'
FAILURE_CONFIRM_STATUS = 'failure'
CANONICAL_FACTS = ['insights_client_id', 'bios_uuid', 'ip_addresses', 'mac_addresses',
                   'vm_uuid', 'etc_machine_id', 'subscription_manager_id']

FAILED_VALIDATION = 'VALIDATION'
FAILED_UPLOAD = 'UPLOAD'
EMPTY_QUEUE_SLEEP = 60
RETRY = Enum('RETRY', 'clear increment keep_same')
RETRIES_ALLOWED = int(RETRIES_ALLOWED)
RETRY_TIME = int(RETRY_TIME)
HOSTS_PER_REQ = 1000


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


class RetryUploadTimeException(Exception):
    """Use to report upload errors that should be retried on time."""

    pass


class RetryUploadCommitException(Exception):
    """Use to report upload errors that should be retried on commit."""

    pass


# pylint: disable=broad-except, too-many-lines
class ReportProcessor():  # pylint: disable=too-many-instance-attributes
    """Class for processing saved reports that have been uploaded."""

    def __init__(self):
        """Create a report processor."""
        self.report = None
        self.state = None
        self.next_state = None
        self.account_number = None
        self.upload_message = None
        self.report_id = None
        self.report_json = None
        self.candidate_hosts = None
        self.failed_hosts = None
        self.status = None
        self.prefix = 'PROCESSING REPORT'
        self.should_run = True

    async def run(self):
        """Run the report processor in a loop.

        Later, if we find that we want to stop loopng, we can
        manipulate the class variable should_run.
        """
        while self.should_run:
            if not self.report:
                self.assign_report()
            if self.report:
                try:
                    await self.delegate_state()
                except Exception as error:
                    LOG.error(format_message(
                        self.prefix,
                        'The following error occurred: %s.' % str(error)))
            else:
                await asyncio.sleep(EMPTY_QUEUE_SLEEP)

    @transaction.atomic
    def assign_report(self):
        """Assign the report processor reports that are saved in the db.

        First priority is the oldest reports that are in the new state.
        If no reports meet this condition, we look for the oldest report in any state.
        We then check to see if an appropriate amount of time has passed before we retry this
        report.
        """
        self.prefix = 'ASSIGNING REPORT'
        report_found_message = 'Starting report processor. State is "%s".'
        if self.report is None:
            try:
                # look for the oldest report in the db
                assign = False
                oldest_report = Report.objects.earliest('last_update_time')
                current_time = datetime.now(pytz.utc)
                status_info = Status()
                same_commit = oldest_report.git_commit == status_info.git_commit
                minutes_passed = int(
                    (current_time - oldest_report.last_update_time).total_seconds() / 60)
                # if the oldest report is a retry based on time and the retry time has
                # passed, then we want to assign the current report
                if oldest_report.retry_type == Report.TIME and minutes_passed >= RETRY_TIME:
                    assign = True
                # or if the oldest report is a retry based on code change and the code
                # has changed, then we want to assign the current report
                elif oldest_report.retry_type == Report.GIT_COMMIT and not same_commit:
                    assign = True
                if assign:
                    self.report = oldest_report
                    self.next_state = oldest_report.state
                    LOG.info(format_message(
                        self.prefix, report_found_message % self.report.state,
                        account_number=self.account_number, report_id=self.report_id))
                    self.update_report_state(retry=RETRY.keep_same)
                else:
                    # else we want to raise an exception to look for reports in the
                    # new state
                    raise QPCReportException()
            except (Report.DoesNotExist, QPCReportException):
                try:
                    # look for the oldest report in the new state
                    self.report = Report.objects.filter(
                        state=Report.NEW).earliest('last_update_time')
                    LOG.info(format_message(self.prefix, report_found_message % self.report.state,
                                            account_number=self.account_number))
                    # update the report time and state
                    self.transition_to_started()
                except Report.DoesNotExist:
                    report_not_found_message = \
                        'No reports to be processed at this time. '\
                        'Checking again in % s seconds.' % str(EMPTY_QUEUE_SLEEP)
                    LOG.info(format_message(self.prefix, report_not_found_message))

    async def delegate_state(self):
        """Call the correct function based on report state.

        If the function is async, make sure to await it.
        """
        self.state = self.report.state
        self.account_number = self.report.rh_account
        self.upload_message = json.loads(self.report.upload_srv_kafka_msg)
        if self.report.candidate_hosts:
            self.candidate_hosts = json.loads(self.report.candidate_hosts)
        if self.report.failed_hosts:
            self.failed_hosts = json.loads(self.report.failed_hosts)
        if self.report.report_json:
            self.report_json = json.loads(self.report.report_json)
        if self.report.report_platform_id:
            self.report_id = self.report.report_platform_id
        if self.report.upload_ack_status:
            self.status = self.report.upload_ack_status
        async_function_call_states = [Report.VALIDATED]
        state_functions = {Report.NEW: self.transition_to_started,
                           Report.STARTED: self.transition_to_downloaded,
                           Report.DOWNLOADED: self.transition_to_validated,
                           Report.VALIDATED: self.transition_to_validation_reported,
                           Report.VALIDATION_REPORTED: self.transition_to_hosts_uploaded,
                           Report.HOSTS_UPLOADED: self.archive_report,
                           Report.FAILED_DOWNLOAD: self.archive_report,
                           Report.FAILED_VALIDATION: self.archive_report,
                           Report.FAILED_VALIDATION_REPORTING: self.archive_report,
                           Report.FAILED_HOSTS_UPLOAD: self.archive_report}
        # if the function is async, we must await it
        if self.state in async_function_call_states:
            await state_functions.get(self.state)()
        else:
            state_functions.get(self.state)()

    def transition_to_started(self):
        """Attempt to change the state to started."""
        self.next_state = Report.STARTED
        self.update_report_state()

    def transition_to_downloaded(self):
        """Attempt to download the report, extract the json & move to downloaded state."""
        self.prefix = 'ATTEMPTING DOWNLOAD'
        report_download_failed_msg = \
            'The report could not be downloaded due to the following error: %s.'
        LOG.info(format_message(
            self.prefix,
            'Attempting to download the report and extract the json. '
            'State is "%s".' % self.report.state,
            account_number=self.account_number))
        try:
            report_tar_gz = self._download_report()
            self.report_json = self._extract_report_from_tar_gz(report_tar_gz)
            self.next_state = Report.DOWNLOADED
            self.update_report_state(report_json=self.report_json)
        except (FailDownloadException, FailExtractException) as err:
            LOG.error(format_message(
                self.prefix,
                report_download_failed_msg % err,
                account_number=self.account_number))
            self.next_state = Report.FAILED_DOWNLOAD
            self.update_report_state()
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
            self.prefix, 'Validating the report contents. State is "%s".' % self.report.state,
            account_number=self.account_number))
        try:
            self.candidate_hosts, self.failed_hosts = self._validate_report_details()
            self.report_id = self.report_json.get('report_platform_id')
            self.status = SUCCESS_CONFIRM_STATUS
            self.next_state = Report.VALIDATED
            self.update_report_state(report_id=self.report_id, candidate_hosts=self.candidate_hosts,
                                     failed_hosts=self.failed_hosts, status=self.status)
            self.deduplicate_reports()
        except QPCReportException:
            # if any QPCReportExceptions occur, we know that the report is not valid but has been
            # successfully validated
            self.status = FAILURE_CONFIRM_STATUS
            self.next_state = Report.VALIDATED
            LOG.warning(format_message(
                self.prefix,
                'The uploaded report was invalid. Status set to "%s".' % self.status,
                account_number=self.account_number))
            self.update_report_state(status=self.status)
        except Exception as error:
            LOG.error(format_message(self.prefix, 'The following error occurred: %s.' % str(error)))
            self.determine_retry(Report.FAILED_VALIDATION, Report.DOWNLOADED,
                                 retry_type=Report.GIT_COMMIT)

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
            self.update_report_state()
            LOG.info(format_message(
                self.prefix,
                'Status successfully uploaded.',
                account_number=self.account_number, report_id=self.report_id))
            if self.status == FAILURE_CONFIRM_STATUS:
                self.archive_report()
        except Exception as error:
            LOG.error(format_message(self.prefix, 'The following error occurred: %s.' % str(error),
                                     account_number=self.account_number, report_id=self.report_id))
            self.determine_retry(Report.FAILED_VALIDATION_REPORTING, Report.VALIDATED)

    def transition_to_hosts_uploaded(self):
        """Upload the host candidates to inventory & move to hosts_uploaded state."""
        self.prefix = 'ATTEMPTING HOST UPLOAD'
        LOG.info(format_message(
            self.prefix, 'Uploading hosts to inventory. State is "%s".' % self.report.state,
            account_number=self.account_number, report_id=self.report_id))
        try:
            if self.candidate_hosts:
                candidates = self.generate_upload_candidates()
                retry_time_candidates, retry_commit_candidates = \
                    self._upload_to_host_inventory(candidates)
                if not retry_time_candidates and not retry_commit_candidates:
                    LOG.info(format_message(self.prefix, 'All hosts were successfully uploaded.',
                                            account_number=self.account_number,
                                            report_id=self.report_id))
                    self.next_state = Report.HOSTS_UPLOADED
                    self.update_report_state(candidate_hosts=[])
                else:
                    candidates = []
                    # if both retry_commit_candidates and retry_time_candidates are returned
                    # (ie. we got both 400 & 500 status codes were returned), we give the
                    # retry_time precedence because we want to retry those with the hope that
                    # they will succeed and leave behind the retry_commit hosts
                    if retry_commit_candidates:
                        candidates += retry_commit_candidates
                        retry_type = Report.GIT_COMMIT
                    if retry_time_candidates:
                        candidates += retry_time_candidates
                        retry_type = Report.TIME
                    LOG.info(format_message(self.prefix, 'Hosts were not successfully uploaded',
                                            account_number=self.account_number,
                                            report_id=self.report_id))
                    self.determine_retry(Report.FAILED_HOSTS_UPLOAD,
                                         Report.VALIDATION_REPORTED,
                                         candidate_hosts=candidates,
                                         retry_type=retry_type)
            else:
                # need to not upload, but archive bc no hosts were valid
                LOG.info(format_message(self.prefix, 'There are no valid hosts to upload',
                                        account_number=self.account_number,
                                        report_id=self.report_id))
                self.archive_report()
        except Exception as error:
            LOG.error(format_message(self.prefix, 'The following error occurred: %s.' % str(error),
                                     account_number=self.account_number, report_id=self.report_id))
            self.determine_retry(Report.FAILED_HOSTS_UPLOAD, Report.VALIDATION_REPORTED,
                                 retry_type=Report.GIT_COMMIT)

    @transaction.atomic
    def archive_report(self):
        """Archive the report object."""
        self.prefix = 'ARCHIVING REPORT'
        LOG.info(format_message(self.prefix, 'Archiving report.',
                                account_number=self.account_number, report_id=self.report_id))
        archived = ReportArchive(
            rh_account=self.account_number,
            retry_count=self.report.retry_count,
            retry_type=self.report.retry_type,
            candidate_hosts=self.report.candidate_hosts,
            failed_hosts=self.report.failed_hosts,
            state=self.state,
            state_info=self.report.state_info,
            last_update_time=self.report.last_update_time,
            upload_srv_kafka_msg=self.upload_message
        )
        if self.report_id:
            archived.report_platform_id = self.report_id
        if self.report_json:
            archived.report_json = self.report_json
        if self.status:
            archived.upload_ack_status = self.status
        archived.save()
        try:
            Report.objects.get(id=self.report.id).delete()
        except Report.DoesNotExist:
            pass
        LOG.info(format_message(self.prefix, 'Report successfully archived.',
                                account_number=self.account_number, report_id=self.report_id))
        self.reset_variables()

    # pylint: disable=too-many-arguments
    def update_report_state(self, retry=RETRY.clear,   # noqa: C901 (too-complex)
                            retry_type=Report.TIME, report_json=None,
                            report_id=None, candidate_hosts=None,
                            failed_hosts=None, status=None):
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
            self.state = self.next_state
            self.report.last_update_time = datetime.now(pytz.utc)
            self.report.state = self.next_state
            self.report.git_commit = status_info.git_commit
            if retry == RETRY.clear:
                # reset the count to 0 (default behavior)
                self.report.retry_count = 0
                self.report.retry_type = Report.TIME
            elif retry == RETRY.increment:
                self.report.retry_count += 1
                self.report.retry_type = retry_type
            # the other choice for retry is RETRY.keep_same in which case we don't
            # want to do anything to the retry count bc we want to preserve as is
            if report_json:
                self.report.report_json = json.dumps(report_json)
            if report_id:
                self.report.report_platform_id = report_id
            if candidate_hosts is not None:
                # candidate_hosts will get smaller and smaller until it hopefully
                # is empty because we have taken care of all ofthe candidates so
                # we rewrite this each time
                self.report.candidate_hosts = json.dumps(candidate_hosts)
            if failed_hosts:
                # for failed hosts this list can keep growing, so we add the
                # newly failed hosts to the previous value
                failed = json.loads(self.report.failed_hosts)
                for host in failed_hosts:
                    failed.append(host)
                self.report.failed_hosts = json.dumps(failed)
            if status:
                self.report.upload_ack_status = status
            state_info = json.loads(self.report.state_info)
            state_info.append(self.next_state)
            self.report.state_info = json.dumps(state_info)
            self.report.save()
        except Exception as error:
            LOG.error(format_message(
                self.prefix,
                'Could not update report record due to the following error %s.' % str(error),
                account_number=self.account_number, report_id=self.report_id))

    def determine_retry(self, fail_state, current_state,
                        candidate_hosts=None, retry_type=Report.TIME):
        """Determine if yupana should archive a report based on retry count.

        :param fail_state: <str> the final state if we have reached max retries
        :param current_state: <str> the current state we are in that we want to try again
        :param candidate_hosts: <list> the updated list of hosts that are still candidates
        :param retry_type: <str> either 'time' or 'commit'
        """
        if (self.report.retry_count + 1) >= RETRIES_ALLOWED:
            LOG.error(format_message(
                self.prefix,
                'This report has reached the retry limit of %s.' % str(RETRIES_ALLOWED),
                account_number=self.account_number, report_id=self.report_id))
            self.next_state = fail_state
            candidates = None
            failed = None
            if self.candidate_hosts:
                self.move_candidates_to_failed()
                candidates = self.candidate_hosts
                failed = self.failed_hosts
            self.update_report_state(retry=RETRY.increment, retry_type=retry_type,
                                     candidate_hosts=candidates,
                                     failed_hosts=failed)
        else:
            self.next_state = current_state
            if retry_type == Report.GIT_COMMIT:
                log_message = \
                    'Saving the report to retry when a new commit '\
                    'is pushed. Retries: %s' % str(self.report.retry_count + 1)
            else:
                log_message = \
                    'Saving the report to retry at in %s minutes. '\
                    'Retries: %s' % (str(RETRY_TIME),
                                     str(self.report.retry_count + 1))
            LOG.error(format_message(
                self.prefix,
                log_message,
                account_number=self.account_number, report_id=self.report_id))

            self.update_report_state(retry=RETRY.increment,
                                     retry_type=retry_type,
                                     candidate_hosts=candidate_hosts)
            self.reset_variables()

    def generate_upload_candidates(self):
        """Generate dictionary of hosts that need to be uploaded to host inventory.

         If a retry has not occurred then we return the candidate_hosts
        but if a retry has occurred and failed at uploading, we want to retry
        the hosts that failed upload while excluding the ones that succeeded.
        """
        candidate_hosts = json.loads(self.report.candidate_hosts)
        candidates = {}
        # we want to generate a dictionary of just the id mapped to the data
        # so we iterate the list creating a dictionary of the key: value if
        # the key is not 'cause' or 'status_code'
        candidates = {key: host[key] for host in candidate_hosts
                      for key in host.keys() if key not in ['cause', 'status_code']}
        return candidates

    def deduplicate_reports(self):
        """If a report with the same id already exists, archive the new report."""
        try:
            existing_reports = Report.objects.filter(
                report_platform_id=self.report.report_platform_id)
            if existing_reports.count() > 1:
                LOG.error(format_message(
                    self.prefix,
                    'a report with the report_platform_id %s already exists.' %
                    self.report.report_platform_id,
                    account_number=self.account_number, report_id=self.report_id))
                self.archive_report()
        except Report.DoesNotExist:
            pass

    def move_candidates_to_failed(self):
        """Before entering a failed state any candidates should be moved to the failed hosts."""
        for host in self.candidate_hosts:
            self.failed_hosts.append(host)
        self.candidate_hosts = []

    def reset_variables(self):
        """Reset the class variables to original values."""
        self.report = None
        self.state = None
        self.account_number = None
        self.upload_message = None
        self.report_id = None
        self.report_json = None
        self.candidate_hosts = None
        self.failed_hosts = None
        self.status = None
        self.prefix = 'PROCESSING REPORT'

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
                'successfully downloaded %s' % report_url,
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

    def _extract_report_from_tar_gz(self, report_tar_gz):  # noqa: C901 (too-complex)
        """Extract Insights report from tar.gz file.

        :param report_tar_gz: A hexstring or BytesIO tarball
            saved in memory with gzip compression.
        :returns: Insights report as dict
        """
        self.prefix = 'EXTRACT REPORT FROM TAR'
        try:
            tar = tarfile.open(fileobj=BytesIO(report_tar_gz), mode='r:gz')
            files_check = tar.getmembers()
            json_files = []
            for file in files_check:
                if '.json' in file.name:
                    json_files.append(file)
            if len(json_files) > 1:
                raise FailExtractException(
                    format_message(self.prefix,
                                   'tar.gz contains multiple files.',
                                   account_number=self.account_number))
            if json_files:
                file = json_files[0]
                tarfile_obj = tar.extractfile(file)
                report_json_str = tarfile_obj.read().decode('utf-8')
                try:
                    insights_report = json.loads(report_json_str)
                    LOG.info(
                        format_message(
                            self.prefix, 'successful',
                            account_number=self.account_number,
                            report_id=insights_report.get('report_platform_id')))
                    return insights_report
                except ValueError as error:
                    raise FailExtractException(
                        format_message(self.prefix,
                                       'Report not JSON. Error: %s' % str(error),
                                       account_number=self.account_number))
            raise FailExtractException(
                format_message(self.prefix,
                               'Tar contains no JSON files.',
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

    def _validate_report_details(self):  # pylint: disable=too-many-locals
        """
        Verify that the report contents are a valid Insights report.

        :returns: tuple contain list of valid and invalid hosts
        """
        self.prefix = 'VALIDATE REPORT STRUCTURE'
        required_keys = ['report_platform_id',
                         'report_id',
                         'report_version',
                         'hosts']
        report_id = self.report_json.get('report_platform_id')

        if self.report_json.get('report_type') != 'insights':
            raise QPCReportException(
                format_message(
                    self.prefix,
                    'Attribute report_type missing or not equal to insights',
                    account_number=self.account_number,
                    report_id=report_id))

        missing_keys = []
        for key in required_keys:
            required_key = self.report_json.get(key)
            if not required_key:
                missing_keys.append(key)

        if missing_keys:
            missing_keys_str = ', '.join(missing_keys)
            raise QPCReportException(
                format_message(
                    self.prefix,
                    'Report is missing required fields: %s.' % missing_keys_str,
                    account_number=self.account_number,
                    report_id=report_id))

        # validate hosts is a dictionary
        invalid_hosts_message = 'Hosts must be a dictionary that is not empty. ' \
                                'All keys must be strings and all values must be dictionaries.'
        hosts = self.report_json.get('hosts')
        if not hosts or not isinstance(hosts, dict):
            raise QPCReportException(
                format_message(
                    self.prefix,
                    invalid_hosts_message,
                    account_number=self.account_number,
                    report_id=report_id))

        invalid_host_dict_format = False
        for host_id, host in hosts.items():
            if not isinstance(host_id, str) or not isinstance(host, dict):
                invalid_host_dict_format = True
                break

        if invalid_host_dict_format:
            raise QPCReportException(
                format_message(
                    self.prefix,
                    invalid_hosts_message,
                    account_number=self.account_number,
                    report_id=report_id))

        candidate_hosts, failed_hosts = self._validate_report_hosts()
        number_valid = len(candidate_hosts)
        total = number_valid + len(failed_hosts)
        LOG.info(format_message(
            self.prefix,
            '%s/%s hosts are valid.' % (
                number_valid, total),
            account_number=self.account_number,
            report_id=report_id
        ))
        if not candidate_hosts:
            raise QPCReportException(
                format_message(
                    self.prefix,
                    'report does not contain any valid hosts.',
                    account_number=self.account_number,
                    report_id=report_id))
        return candidate_hosts, failed_hosts

    def _validate_report_hosts(self):
        """Verify that report hosts contain canonical facts.

        :returns: tuple containing valid & invalid hosts
        """
        hosts = self.report_json['hosts']
        report_id = self.report_json['report_platform_id']

        prefix = 'VALIDATE HOSTS'
        invalid_hosts = {}
        candidate_hosts = []
        failed_hosts = []
        for host_id, host in hosts.items():
            found_facts = False
            for fact in CANONICAL_FACTS:
                if host.get(fact):
                    found_facts = True
                    break
            if found_facts:
                candidate_hosts.append({host_id: host})
            else:
                host.pop('metadata', None)
                failed_hosts.append({host_id: host,
                                     'cause': FAILED_VALIDATION})
                invalid_hosts[host_id] = host
        if invalid_hosts:
            LOG.warning(
                format_message(
                    prefix,
                    'Removed %d hosts with 0 canonical facts: %s' % (
                        len(invalid_hosts), invalid_hosts),
                    account_number=self.account_number,
                    report_id=report_id))

        return candidate_hosts, failed_hosts

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
            loop=PROCESSING_LOOP, bootstrap_servers=INSIGHTS_KAFKA_ADDRESS
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

    @staticmethod
    def format_certs(redhat_certs):
        """Strip the .pem from each cert in the list.

        :param redhat_certs: <list> of redhat certs.
        :returns: <list> of formatted certs.
        """
        try:
            return [int(cert.strip('.pem')) for cert in redhat_certs if cert]
        except Exception:  # pylint: disable=broad-except
            return []

    @staticmethod
    def format_products(redhat_products, is_rhel):
        """Return the installed products on the system.

        :param redhat_products: <dict> of products.
        :returns: a list of the installed products.
        """
        products = []
        name_to_product = {'JBoss EAP': 'EAP',
                           'JBoss Fuse': 'FUSE',
                           'JBoss BRMS': 'DCSM',
                           'JBoss Web Server': 'JWS'}
        if is_rhel:
            products.append('RHEL')
        for product_dict in redhat_products:
            if product_dict.get('presence') == 'present':
                name = name_to_product.get(product_dict.get('name'))
                if name:
                    products.append(name)

        return products

    @staticmethod
    def format_system_profile(host):
        """Grab facts from original host for system profile.

        :param host: <dict> the host to pull facts from
        :returns: a list with the system profile facts.
        """
        qpc_to_system_profile = {
            'infrastructure_type': 'infrastructure_type',
            'architecture': 'arch',
            'os_release': 'os_release',
            'os_version': 'os_kernel_version',
            'vm_host': 'infrastructure_vendor'
        }
        system_profile = {}
        for qpc_fact, system_fact in qpc_to_system_profile.items():
            fact_value = host.get(qpc_fact)
            if fact_value:
                system_profile[system_fact] = str(fact_value)
        cpu_count = host.get('cpu_count')
        # grab the default socket count
        cpu_socket_count = host.get('cpu_socket_count')
        # grab the preferred socket count, and default if it does not exist
        socket_count = host.get('vm_host_socket_count', cpu_socket_count)
        # grab the default core count
        cpu_core_count = host.get('cpu_core_count')
        # grab the preferred core count, and default if it does not exist
        core_count = host.get('vm_host_core_count', cpu_core_count)
        try:
            # try to get the cores per socket but wrap it in a try/catch
            # because these values might not exist
            core_per_socket = math.ceil(int(core_count) / int(socket_count))
        except Exception:  # pylint: disable=broad-except
            core_per_socket = None
        # grab the preferred core per socket, but default if it does not exist
        cpu_core_per_socket = host.get('cpu_core_per_socket', core_per_socket)
        # check for each of the above facts and add them to the profile if they
        # are not none
        if cpu_count:
            system_profile['number_of_cpus'] = math.ceil(cpu_count)
        if socket_count:
            system_profile['number_of_sockets'] = math.ceil(socket_count)
        if cpu_core_per_socket:
            system_profile['cores_per_socket'] = math.ceil(cpu_core_per_socket)

        return system_profile

    def generate_bulk_upload_list(self, hosts):  # pylint:disable=too-many-locals
        """Generate a list of hosts to upload.

        :param hosts: <dict> dictionary containing hosts to upload.
        """
        bulk_upload_list = []
        non_null_facts = \
            ['bios_uuid', 'ip_addresses',
             'mac_addresses', 'insights_client_id',
             'rhel_machine_id', 'subscription_manager_id']
        for _, host in hosts.items():
            redhat_certs = host.get('redhat_certs', [])
            redhat_products = host.get('products', [])
            is_redhat = host.get('is_redhat')
            system_profile = self.format_system_profile(host)
            formatted_certs = self.format_certs(redhat_certs)
            formatted_products = self.format_products(redhat_products,
                                                      is_redhat)

            body = {
                'account': self.account_number,
                'display_name': host.get('name'),
                'fqdn': host.get('name'),
                'facts': [{'namespace': 'qpc', 'facts': host,
                           'rh_product_certs': formatted_certs,
                           'rh_products_installed': formatted_products}]
            }
            if system_profile:
                body['system_profile'] = system_profile
            for fact_name in non_null_facts:
                fact_value = host.get(fact_name)
                if fact_value:
                    body[fact_name] = fact_value

            bulk_upload_list.append(body)
        return bulk_upload_list

    @staticmethod
    def split_hosts(list_of_all_hosts):
        """Split up the hosts into lists of 1000 or less."""
        hosts_per_request = HOSTS_PER_REQ
        hosts_lists_to_upload = \
            [list_of_all_hosts[i:i + hosts_per_request]
             for i in range(0, len(list_of_all_hosts), hosts_per_request)]
        return hosts_lists_to_upload

    # pylint: disable=too-many-branches, too-many-statements
    def _upload_to_host_inventory(self, hosts):  # noqa: C901 (too-complex) pylint: disable=too-many-locals
        """
        Verify that the report contents are a valid Insights report.

        :param hosts: a list of dictionaries that have been validated.
        :returns None
        """
        self.prefix = 'UPLOAD TO HOST INVENTORY'
        identity_string = '{"identity": {"account_number": "%s"}}' % str(self.account_number)
        bytes_string = identity_string.encode()
        x_rh_identity_value = base64.b64encode(bytes_string).decode()
        identity_header = {'x-rh-identity': x_rh_identity_value,
                           'Content-Type': 'application/json'}
        list_of_all_hosts = self.generate_bulk_upload_list(hosts)
        hosts_lists_to_upload = self.split_hosts(list_of_all_hosts)
        failed_hosts = []  # this is purely for counts and logging
        retry_time_hosts = []  # storing hosts to retry after time
        retry_commit_hosts = []  # storing hosts to retry after commit change
        group_count = 0
        for hosts_list in hosts_lists_to_upload:  # pylint: disable=too-many-nested-blocks
            group_count += 1
            LOG.info(format_message(
                self.prefix,
                'Uploading hosts group %s/%s. Group size: %s hosts' %
                (group_count, len(hosts_lists_to_upload), HOSTS_PER_REQ),
                account_number=self.account_number, report_id=self.report_id))
            try:  # pylint: disable=too-many-nested-blocks
                response = requests.post(INSIGHTS_HOST_INVENTORY_URL,
                                         data=json.dumps(hosts_list),
                                         headers=identity_header)

                if response.status_code in [HTTPStatus.MULTI_STATUS]:
                    try:
                        json_body = response.json()
                    except ValueError:
                        # something went wrong
                        raise RetryUploadTimeException(format_message(
                            self.prefix, 'Missing json response',
                            account_number=self.account_number, report_id=self.report_id))
                    errors = json_body.get('errors')
                    if errors != 0:
                        all_data = json_body.get('data', [])
                        host_index = 0
                        for host_data in all_data:
                            host_status = host_data.get('status')
                            if host_status not in [HTTPStatus.OK, HTTPStatus.CREATED]:
                                upload_host = hosts_list[host_index]
                                host_facts = upload_host.get('facts')
                                for namespace_facts in host_facts:
                                    if namespace_facts.get('namespace') == 'qpc':
                                        qpc_facts = namespace_facts.get('facts')
                                        host_id = qpc_facts.get('system_platform_id')
                                        original_host = hosts.get(host_id, {})
                                failed_hosts.append({
                                    'status_code': host_status,
                                    'display_name': original_host.get('name'),
                                    'system_platform_id': host_id,
                                    'host': original_host})

                                # if the response code is a 500, then something on
                                # host inventory side blew up and we want to retry
                                # after a certain amount of time
                                if str(host_status).startswith('5'):
                                    retry_time_hosts.append({host_id: original_host,
                                                             'cause': FAILED_UPLOAD,
                                                             'status_code': host_status})
                                else:
                                    # else, if we recieved a 400 status code, the problem is
                                    # likely on our side so we should retry after a code change
                                    retry_commit_hosts.append({host_id: original_host,
                                                               'cause': FAILED_UPLOAD,
                                                               'status_code': host_status})
                            host_index += 1

                elif str(response.status_code).startswith('5'):
                    # something went wrong on host inventory side and we should regenerate after
                    # some time has passed
                    message = 'Attempted to upload the following: %s' % str(hosts_list)
                    LOG.error(format_message(self.prefix, message,
                                             account_number=self.account_number,
                                             report_id=self.report_id))
                    try:
                        LOG.error(response.json())
                    except ValueError:
                        LOG.error('No response json')
                    LOG.error(format_message(
                        self.prefix,
                        'Unexpected response code %s' % str(response.status_code),
                        account_number=self.account_number, report_id=self.report_id))
                    raise RetryUploadTimeException()
                else:
                    # something went wrong possibly on our side (if its a 400)
                    # and we should regenerate the hosts dictionary and re-upload after a commit
                    message = 'Attempted to upload the following: %s' % str(hosts_list)
                    LOG.error(format_message(self.prefix, message,
                                             account_number=self.account_number,
                                             report_id=self.report_id))
                    try:
                        LOG.error(response.json())
                    except ValueError:
                        LOG.error('No response json')
                    LOG.error(format_message(
                        self.prefix,
                        'Unexpected response code %s' % str(response.status_code),
                        account_number=self.account_number,
                        report_id=self.report_id))
                    raise RetryUploadCommitException()

            except RetryUploadCommitException:
                for host_id, host_data in hosts.items():
                    retry_commit_hosts.append({host_id: host_data,
                                               'cause': FAILED_UPLOAD})
                    failed_hosts.append({
                        'status_code': 'unknown',
                        'display_name': host_data.get('name'),
                        'system_platform_id': host_id,
                        'host': host_data})
            except RetryUploadTimeException:
                for host_id, host_data in hosts.items():
                    retry_time_hosts.append({host_id: host_data,
                                             'cause': FAILED_UPLOAD})
                    failed_hosts.append({
                        'status_code': 'unknown',
                        'display_name': host_data.get('name'),
                        'system_platform_id': host_id,
                        'host': host_data})

            except requests.exceptions.RequestException as err:
                LOG.error(format_message(self.prefix, 'An error occurred: %s' % str(err),
                                         account_number=self.account_number,
                                         report_id=self.report_id))
                for host_id, host_data in hosts.items():
                    retry_time_hosts.append({host_id: host_data,
                                             'cause': FAILED_UPLOAD})
                    failed_hosts.append({
                        'status_code': 'unknown',
                        'display_name': host_data.get('name'),
                        'system_platform_id': host_id,
                        'host': host_data})

        successful = len(hosts) - len(failed_hosts)
        upload_msg = format_message(
            self.prefix, '%s/%s hosts uploaded to host inventory' %
            (successful, len(hosts)),
            account_number=self.account_number,
            report_id=self.report_id
        )
        if successful != len(hosts):
            LOG.warning(upload_msg)
        else:
            LOG.info(upload_msg)
        if failed_hosts:
            for failed_info in failed_hosts:
                LOG.error(format_message(
                    self.prefix,
                    'Host inventory returned %s for %s. '
                    'system_platform_id: %s. host: %s' % (
                        failed_info.get('status_code'),
                        failed_info.get('display_name'),
                        failed_info.get('system_platform_id'),
                        failed_info.get('host')),
                    account_number=self.account_number,
                    report_id=self.report_id
                ))
        return retry_time_hosts, retry_commit_hosts


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
                                         args=(PROCESSING_LOOP,))
    event_loop_thread.daemon = True
    event_loop_thread.start()
