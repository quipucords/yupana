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
import json
import logging
from abc import ABC
from datetime import datetime, timedelta
from enum import Enum

import pytz
from django.db import transaction
from processor.kafka_msg_handler import (QPCReportException,
                                         format_message)
from prometheus_client import Counter, Gauge, Summary

from api.models import (Report,
                        ReportSlice,
                        Status)
from api.serializers import (ReportArchiveSerializer,
                             ReportSliceArchiveSerializer)
from config.settings.base import (RETRIES_ALLOWED, RETRY_TIME)

LOG = logging.getLogger(__name__)
FAILURE_CONFIRM_STATUS = 'failure'
CANONICAL_FACTS = ['insights_client_id', 'bios_uuid', 'ip_addresses', 'mac_addresses',
                   'vm_uuid', 'etc_machine_id', 'subscription_manager_id']

FAILED_VALIDATION = 'VALIDATION'
EMPTY_QUEUE_SLEEP = 60
RETRY = Enum('RETRY', 'clear increment keep_same')
RETRIES_ALLOWED = int(RETRIES_ALLOWED)
RETRY_TIME = int(RETRY_TIME)

# setup for prometheus metrics
QUEUED_OBJECTS = Gauge('queued_objects', 'Reports & Report slices waiting to be processed')
ARCHIVED_FAIL = Counter('archived_fail', 'Reports that have been archived as failures')
ARCHIVED_SUCCESS = Counter('archived_success', 'Reports that have been archived as successes')
FAILED_TO_DOWNLOAD = Counter('failed_download', 'Reports that failed to downlaod')
FAILED_TO_VALIDATE = Counter('failed_validation', 'Reports that could not be validated')
INVALID_REPORTS = Counter('invalid_reports', 'Reports containing invalid syntax')
TIME_RETRIES = Counter('time_retries', 'The total number of retries based on time for all reports')
COMMIT_RETRIES = Counter('commit_retries',
                         'The total number of retries based on commit for all reports')
HOST_UPLOAD_REQUEST_LATENCY = Summary(
    'inventory_upload_latency',
    'The time in seconds that it takes to post to the host inventory')
UPLOAD_GROUP_SIZE = Gauge('upload_group_size',
                          'The amount of hosts being uploaded in a single bulk request.')
VALIDATION_LATENCY = Summary('validation_latency', 'The time it takes to validate a report')
INVALID_HOSTS = Gauge('invalid_hosts_per_report', 'The total number of invalid hosts per report')
VALID_HOSTS = Gauge('valid_hosts_per_report', 'The total number of valid hosts per report')
HOSTS_UPLOADED_SUCCESS = Gauge('hosts_uploaded', 'The total number of hosts successfully uploaded')
HOSTS_UPLOADED_FAILED = Gauge('hosts_failed', 'The total number of hosts that fail to upload')


# pylint: disable=broad-except, too-many-lines, too-many-public-methods
class AbstractProcessor(ABC):  # pylint: disable=too-many-instance-attributes
    """Class for processing saved reports that have been uploaded."""

    # pylint: disable=too-many-arguments
    def __init__(self, pre_delegate, state_functions, state_metrics,
                 async_states, object_prefix, object_class,
                 object_serializer):
        """Create an abstract processor."""
        self.report_or_slice = None
        self.object_class = object_class
        self.object_serializer = object_serializer
        self.run_before_delegate = pre_delegate
        self.state_functions = state_functions
        self.state_to_metric = state_metrics
        self.async_states = async_states
        self.object_prefix = object_prefix
        self.prefix = 'PROCESSING %s' % self.object_prefix
        self.state = None
        self.next_state = None
        self.account_number = None
        self.upload_message = None
        self.report_platform_id = None
        self.report_json = None
        self.candidate_hosts = None
        self.failed_hosts = None
        self.status = None
        self.report = None
        self.should_run = True

    def reset_variables(self):
        """Reset the class variables to original values."""
        self.report_or_slice = None
        self.state = None
        self.account_number = None
        self.upload_message = None
        self.report_platform_id = None
        self.report_json = None
        self.candidate_hosts = None
        self.failed_hosts = None
        self.status = None
        self.report = None
        self.prefix = 'PROCESSING %s' % self.object_prefix

    async def run(self):
        """Run the report processor in a loop.

        Later, if we find that we want to stop looping, we can
        manipulate the class variable should_run.
        """
        while self.should_run:
            if not self.report_or_slice:
                self.assign_object()
            if self.report_or_slice:
                try:
                    await self.delegate_state()
                except Exception as error:
                    LOG.error(format_message(
                        self.prefix,
                        'The following error occurred: %s.' % str(error)))
                    self.reset_variables()
            else:
                await asyncio.sleep(EMPTY_QUEUE_SLEEP)

    def calculate_queued_objects(self, current_time, status_info):
        """Calculate the number of reports waiting to be processed.

        :param current_time: time object.
        :param status_info: status object.
        """
        minimum_update_time = current_time - timedelta(minutes=RETRY_TIME)
        # we want to grab all the new reports, or all the reports that are ready to be retried
        # based on time, or commit
        all_objects = self.object_class.objects.all()
        new_objects = all_objects.filter(state=self.object_class.NEW)
        retry_time_objects = all_objects.filter(
            retry_type=self.object_class.TIME,
            last_update_time__lte=minimum_update_time).exclude(state=self.object_class.NEW)
        retry_commit_objects = all_objects.filter(
            retry_type=self.object_class.GIT_COMMIT).exclude(
                state=self.object_class.NEW).exclude(git_commit=status_info.git_commit)
        objects_count = \
            new_objects.count() + retry_time_objects.count() + retry_commit_objects.count()

        return objects_count

    @staticmethod
    def return_queryset_object(queryset):
        """Return the earliest object in a queryset.

        param queryset: the queryset we care about
        returns: the earliest object in the queryset or None.
        """
        try:
            report_or_slice = queryset.earliest('last_update_time')
            return report_or_slice
        except (Report.DoesNotExist, ReportSlice.DoesNotExist):
            return None

    def get_oldest_object_to_retry(self):
        """Grab the oldest report or report slice object to retry.

        returns: object to retry or None.
        """
        status_info = Status()
        current_time = datetime.now(pytz.utc)
        objects_count = self.calculate_queued_objects(current_time, status_info)
        QUEUED_OBJECTS.set(objects_count)
        LOG.info(format_message(
            self.prefix,
            'Number of %s waiting to be processed: %s' %
            (self.object_prefix.lower() + 's', objects_count)))
        # first we have to query for all objects with commit retries
        commit_retry_query = self.object_class.objects.filter(
            retry_type=self.object_class.GIT_COMMIT)
        # then we grab the oldest object from the query
        oldest_commit_object = self.return_queryset_object(queryset=commit_retry_query)
        if oldest_commit_object:
            same_commit = oldest_commit_object.git_commit == status_info.git_commit
            if not same_commit:
                return oldest_commit_object
        # If the above doesn't return, we should query for all time retries
        time_retry_query = self.object_class.objects.filter(
            retry_type=self.object_class.TIME)
        oldest_time_object = self.return_queryset_object(queryset=time_retry_query)
        if oldest_time_object:
            minutes_passed = int(
                (current_time - oldest_time_object.last_update_time).total_seconds() / 60)
            if minutes_passed >= RETRY_TIME:
                return oldest_time_object
        # if we haven't returned a retry object, return None
        return None

    def get_new_record(self):
        """Grab the newest report or report slice object."""
        # Get the queryset for all of the objects in the NEW state
        new_object_query = self.object_class.objects.filter(
            state=self.object_class.NEW)
        oldest_new_object = self.return_queryset_object(queryset=new_object_query)
        return oldest_new_object

    @transaction.atomic
    def assign_object(self):
        """Assign the object processor objects that are saved in the db.

        First priority is the oldest object in any state. We check to see if an
        appropriate amount of time has passed  or code has changed before we retry this object.

        If none of the above qualify, we look for the oldest objects that are in the new state.
        """
        self.prefix = 'ASSIGNING %s' % self.object_prefix
        object_found_message = 'Starting %s processor. State is "%s".'
        if self.report_or_slice is None:
            assigned = False
            oldest_object_to_retry = self.get_oldest_object_to_retry()
            if oldest_object_to_retry:
                assigned = True
                self.report_or_slice = oldest_object_to_retry
                self.next_state = oldest_object_to_retry.state
                LOG.info(format_message(
                    self.prefix, object_found_message % (self.object_prefix.lower(),
                                                         self.report_or_slice.state),
                    account_number=self.account_number,
                    report_platform_id=self.report_or_slice.report_platform_id))
                options = {'retry': RETRY.keep_same}
                self.update_object_state(options=options)
            else:
                new_object = self.get_new_record()
                if new_object:
                    assigned = True
                    self.report_or_slice = new_object
                    LOG.info(
                        format_message(
                            self.prefix, object_found_message % (self.object_prefix.lower(),
                                                                 self.report_or_slice.state),
                            account_number=self.account_number,
                            report_platform_id=self.report_or_slice.report_platform_id))
                    self.transition_to_started()
            if not assigned:
                object_not_found_message = \
                    'No %s to be processed at this time. '\
                    'Checking again in %s seconds.' \
                    % (self.object_prefix.lower() + 's', str(EMPTY_QUEUE_SLEEP))
                LOG.info(format_message(self.prefix, object_not_found_message))

    async def delegate_state(self):
        """Call the correct function based on report state.

        If the function is async, make sure to await it.
        """
        self.run_before_delegate()
        # if the function is async, we must await it
        if self.state_functions.get(self.state):
            if self.state in self.async_states:
                await self.state_functions.get(self.state)()
            else:
                self.state_functions.get(self.state)()
        else:
            self.reset_variables()

    def transition_to_started(self):
        """Attempt to change the state to started."""
        self.next_state = self.object_class.STARTED
        self.update_object_state(options={})

    #  pylint: disable=too-many-locals, too-many-branches, too-many-statements
    def update_object_state(self, options):  # noqa: C901 (too-complex)
        """
        Update the report processor state and save.

        :param options: <dict> containing potentially containing the following:
            retry: <enum> Retry.clear=clear count, RETRY.increment=increase count
            retry_type: <str> either time=retry after time,
                git_commit=retry after new commit
            report_json: <dict> dictionary containing the report json
            report_platform_id: <str> string containing report_platform_id
            candidate_hosts: <dict> dictionary containing hosts that were
                successfully verified and uploaded
            failed_hosts: <dict> dictionary containing hosts that failed
                verification or upload
            status: <str> either success or failure based on the report
            report_type: <str> the type of the report
            report_id: <int> the report id
            report_version: <str> the report version
            ready_to_archive: <bool> bool regarding archive
        """
        try:
            status_info = Status()
            self.state = self.next_state

            # grab all of the potential options
            retry_type = options.get('retry_type', self.object_class.TIME)
            retry = options.get('retry', RETRY.clear)
            report_json = options.get('report_json')
            report_platform_id = options.get('report_platform_id')
            candidate_hosts = options.get('candidate_hosts')
            failed_hosts = options.get('failed_hosts')
            status = options.get('status')
            report_type = options.get('report_type')
            report_id = options.get('report_id')
            report_version = options.get('report_version')
            ready_to_archive = options.get('ready_to_archive')

            update_data = {
                'last_update_time': datetime.now(pytz.utc),
                'state': self.next_state,
                'git_commit': status_info.git_commit
            }

            if retry == RETRY.clear:
                # After a successful transaction when we have reached the update
                # point, we want to set the Retry count back to 0 because
                # any future failures should be unrelated
                update_data['retry_count'] = 0
                update_data['retry_type'] = self.object_class.TIME
            elif retry == RETRY.increment:
                retry_count = self.report_or_slice.retry_count
                update_data['retry_count'] = retry_count + 1
                update_data['retry_type'] = retry_type

            # the other choice for retry is RETRY.keep_same in which case we don't
            # want to do anything to the retry count bc we want to preserve as is
            if report_json:
                update_data['report_json'] = json.dumps(report_json)
            if report_platform_id:
                update_data['report_platform_id'] = report_platform_id
            if candidate_hosts is not None:
                # candidate_hosts will get smaller and smaller until it hopefully
                # is empty because we have taken care of all ofthe candidates so
                # we rewrite this each time
                update_data['candidate_hosts'] = json.dumps(candidate_hosts)
            if failed_hosts:
                # for failed hosts this list can keep growing, so we add the
                # newly failed hosts to the previous value
                failed = json.loads(self.report_or_slice.failed_hosts)
                for host in failed_hosts:
                    failed.append(host)
                update_data['failed_hosts'] = json.dumps(failed)
            if status:
                update_data['upload_ack_status'] = status
            if report_type:
                update_data['report_type'] = report_type
            if report_id:
                update_data['report_id'] = report_id
            if report_version:
                update_data['report_version'] = report_version
            if ready_to_archive:
                update_data['ready_to_archive'] = ready_to_archive

            state_info = json.loads(self.report_or_slice.state_info)
            state_info.append(self.next_state)
            update_data['state_info'] = json.dumps(state_info)

            serializer = self.object_serializer(
                instance=self.report_or_slice,
                data=update_data,
                partial=True)

            if serializer.is_valid(raise_exception=True):
                serializer.save()

        except Exception as error:
            LOG.error(format_message(
                self.prefix,
                'Could not update %s record due to the following error %s.' % (
                    self.object_prefix.lower(), str(error)),
                account_number=self.account_number, report_platform_id=self.report_platform_id))

    def move_candidates_to_failed(self):
        """Before entering a failed state any candidates should be moved to the failed hosts."""
        for host in self.candidate_hosts:
            self.failed_hosts.append(host)
        self.candidate_hosts = []

    def determine_retry(self, fail_state, current_state,
                        candidate_hosts=None, retry_type=Report.TIME):
        """Determine if yupana should archive a report based on retry count.

        :param fail_state: <str> the final state if we have reached max retries
        :param current_state: <str> the current state we are in that we want to try again
        :param candidate_hosts: <list> the updated list of hosts that are still candidates
        :param retry_type: <str> either 'time' or 'commit'
        """
        if (self.report_or_slice.retry_count + 1) >= RETRIES_ALLOWED:
            LOG.error(format_message(
                self.prefix,
                'This %s has reached the retry limit of %s.'
                % (self.object_prefix.lower(), str(RETRIES_ALLOWED)),
                account_number=self.account_number, report_platform_id=self.report_platform_id))
            self.next_state = fail_state
            candidates = None
            failed = None
            if self.candidate_hosts:
                self.move_candidates_to_failed()
                candidates = self.candidate_hosts
                failed = self.failed_hosts
            options = {'retry': RETRY.increment, 'retry_type': retry_type,
                       'candidate_hosts': candidates, 'failed_hosts': failed,
                       'ready_to_archive': True}
            self.update_object_state(options=options)
        else:
            self.next_state = current_state
            if retry_type == self.object_class.GIT_COMMIT:
                COMMIT_RETRIES.inc()
                log_message = \
                    'Saving the %s to retry when a new commit '\
                    'is pushed. Retries: %s' % (self.object_prefix.lower(),
                                                str(self.report_or_slice.retry_count + 1))
            else:
                TIME_RETRIES.inc()
                log_message = \
                    'Saving the %s to retry at in %s minutes. '\
                    'Retries: %s' % (self.object_prefix.lower(),
                                     str(RETRY_TIME),
                                     str(self.report_or_slice.retry_count + 1))
            LOG.error(format_message(
                self.prefix,
                log_message,
                account_number=self.account_number, report_platform_id=self.report_platform_id))

            options = {'retry': RETRY.increment, 'retry_type': retry_type,
                       'candidate_hosts': candidate_hosts}
            self.update_object_state(options=options)
            self.reset_variables()

    def record_failed_state_metrics(self):
        """Record the metrics based on the report or slice state."""
        if self.state in self.state_to_metric.keys():
            self.state_to_metric.get(self.state)()

    @transaction.atomic  # noqa: C901 (too-complex)
    def archive_report_and_slices(self):  # pylint: disable=too-many-statements
        """Archive the report slice objects & associated report."""
        self.prefix = 'ARCHIVING'
        if self.object_class == Report:
            report = self.report_or_slice
        else:
            report = self.report_or_slice.report
        all_report_slices = []
        all_slices_ready = True
        try:
            all_report_slices = ReportSlice.objects.all().filter(report=report)
            for report_slice in all_report_slices:
                if not report_slice.ready_to_archive:
                    all_slices_ready = False
                    break
        except ReportSlice.DoesNotExist:
            pass

        if report.ready_to_archive and all_slices_ready:
            # archive the report object
            failed = False
            LOG.info(format_message(self.prefix, 'Archiving report.',
                                    account_number=self.account_number,
                                    report_platform_id=self.report_platform_id))
            archived_rep_data = {
                'rh_account': report.rh_account,
                'retry_count': report.retry_count,
                'retry_type': report.retry_type,
                'state': report.state,
                'state_info': report.state_info,
                'ready_to_archive': report.ready_to_archive,
                'last_update_time': report.last_update_time,
                'upload_srv_kafka_msg': report.upload_srv_kafka_msg,
            }
            if report.upload_ack_status:
                if report.upload_ack_status == FAILURE_CONFIRM_STATUS:
                    failed = True
                    INVALID_REPORTS.inc()
                archived_rep_data['upload_ack_status'] = report.upload_ack_status
            if report.report_platform_id:
                archived_rep_data['report_platform_id'] = report.report_platform_id
            rep_serializer = ReportArchiveSerializer(data=archived_rep_data)
            if rep_serializer.is_valid(raise_exception=True):
                archived_rep = rep_serializer.save()
                LOG.info(format_message(self.prefix, 'Report successfully archived.',
                                        account_number=self.account_number,
                                        report_platform_id=self.report_platform_id))

            failed_states = [Report.FAILED_DOWNLOAD, Report.FAILED_VALIDATION,
                             Report.FAILED_VALIDATION_REPORTING]
            if report.state in failed_states or failed:
                ARCHIVED_FAIL.inc()
            else:
                ARCHIVED_SUCCESS.inc()

            # loop through the associated reports & archive them
            for report_slice in all_report_slices:
                archived_slice_data = {
                    'rh_account': report_slice.rh_account,
                    'retry_count': report_slice.retry_count,
                    'retry_type': report_slice.retry_type,
                    'candidate_hosts': report_slice.candidate_hosts,
                    'failed_hosts': report_slice.failed_hosts,
                    'state': report_slice.state,
                    'ready_to_archive': report_slice.ready_to_archive,
                    'state_info': report_slice.state_info,
                    'last_update_time': report_slice.last_update_time,
                    'report_slice_id': report_slice.report_slice_id,
                    'report': archived_rep.id
                }
                if report_slice.report_platform_id:
                    archived_slice_data['report_platform_id'] = report_slice.report_platform_id
                if report_slice.report_json:
                    archived_slice_data['report_json'] = report_slice.report_json
                slice_serializer = ReportSliceArchiveSerializer(data=archived_slice_data)
                if slice_serializer.is_valid(raise_exception=True):
                    slice_serializer.save()
                failed_states = [ReportSlice.FAILED_VALIDATION, ReportSlice.FAILED_HOSTS_UPLOAD]
                if report_slice.state in failed_states:
                    ARCHIVED_FAIL.inc()
                else:
                    ARCHIVED_SUCCESS.inc()
                LOG.info(format_message(
                    self.prefix,
                    'Archiving report slice %s.' % report_slice.report_slice_id,
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id))
            self.record_failed_state_metrics()
            # now delete the report object and it will delete all of the associated
            # report slices
            try:
                Report.objects.get(id=report.id).delete()
            except Report.DoesNotExist:
                pass
            if all_report_slices:
                LOG.info(format_message(self.prefix, 'Report slices successfully archived.',
                                        account_number=self.account_number,
                                        report_platform_id=self.report_platform_id))
            self.reset_variables()

        else:
            LOG.info(format_message(self.prefix,
                                    'Could not archive report because one or more associated slices'
                                    ' are still being processed.',
                                    account_number=self.account_number,
                                    report_platform_id=self.report_platform_id))
            self.reset_variables()

    def _validate_report_details(self):  # pylint: disable=too-many-locals
        """
        Verify that the report contents are a valid Insights report.

        :returns: tuple contain list of valid and invalid hosts
        """
        self.prefix = 'VALIDATE REPORT STRUCTURE'
        required_keys = ['report_platform_id',
                         'report_slice_id',
                         'hosts']
        report_platform_id = self.report_json.get('report_platform_id')

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
                    report_platform_id=report_platform_id))

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
                    report_platform_id=report_platform_id))

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
                    report_platform_id=report_platform_id))

        candidate_hosts, failed_hosts = self._validate_report_hosts()
        number_valid = len(candidate_hosts)
        total = number_valid + len(failed_hosts)
        LOG.info(format_message(
            self.prefix,
            '%s/%s hosts are valid.' % (
                number_valid, total),
            account_number=self.account_number,
            report_platform_id=report_platform_id
        ))
        if not candidate_hosts:
            raise QPCReportException(
                format_message(
                    self.prefix,
                    'report does not contain any valid hosts.',
                    account_number=self.account_number,
                    report_platform_id=report_platform_id))
        return candidate_hosts, failed_hosts

    def _validate_report_hosts(self):
        """Verify that report hosts contain canonical facts.

        :returns: tuple containing valid & invalid hosts
        """
        hosts = self.report_json['hosts']
        report_platform_id = self.report_json['report_platform_id']

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
                    report_platform_id=report_platform_id))

        return candidate_hosts, failed_hosts
