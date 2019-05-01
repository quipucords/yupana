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
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
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
from prometheus_client import Counter, Gauge, Summary

from api.models import (Report, ReportArchive, 
                        ReportSlice, ReportSliceArchive, 
                        Status)
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
MAX_HOSTS_PER_REP = 10000

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

    def __init__(self, pre_delegate, state_functions, 
                async_states, object_prefix, object_class):
        """Create an abstract processor."""
        self.report_or_slice = None
        self.object_class = object_class
        self.pre_delegate = pre_delegate
        self.state_functions = state_functions
        self.async_states = async_states
        self.object_prefix = object_prefix
        self.prefix = 'PROCESSING %s' % self.object_prefix
        self.state = None
        self.next_state = None
        self.account_number = None
        self.upload_message = None
        self.report_id = None
        self.report_json = None
        self.candidate_hosts = None
        self.failed_hosts = None
        self.status = None
        self.should_run = True

    def reset_variables(self):
        """Reset the class variables to original values."""
        print('RESETTING THE VARIABLES in lieu of archiving')
        self.report_or_slice = None
        self.state = None
        self.account_number = None
        self.upload_message = None
        self.report_id = None
        self.report_json = None
        self.candidate_hosts = None
        self.failed_hosts = None
        self.status = None
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

    def transition_to_started(self):
        """Attempt to change the state to started."""
        self.next_state = self.object_class.STARTED
        print('made it to started')
        self.update_object_state()

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

    def get_oldest_object_retry(self):
        """Grab the oldest report or report slice object.
        
        returns: boolean and oldest report or report slice object.
        """
        assign = False
        current_time = datetime.now(pytz.utc)
        status_info = Status()
        objects_count = self.calculate_queued_objects(current_time, status_info)
        QUEUED_OBJECTS.set(objects_count)
        LOG.info(format_message(
            self.prefix,
            'Number of %s waiting to be processed: %s' % 
            (self.object_prefix.lower() + 's', objects_count)))
        # look for the oldest object in the db
        #check now
        oldest_object = self.object_class.objects.earliest('last_update_time')
        same_commit = oldest_object.git_commit == status_info.git_commit
        minutes_passed = int(
            (current_time - oldest_object.last_update_time).total_seconds() / 60)
        # if the oldest object is a retry based on time and the retry time has
        # passed, then we want to assign the current object
        if oldest_object.retry_type == self.object_class.TIME and minutes_passed >= RETRY_TIME:
            assign = True
        # or if the oldest object is a retry based on code change and the code
        # has changed, then we want to assign the current object
        elif oldest_object.retry_type == self.object_class.GIT_COMMIT and not same_commit:
            assign = True
        return assign, oldest_object

    def get_new_record(self):
        """Grab the newest report or report slice object."""
        # look for the oldest report in the new state
        self.report_or_slice = self.object_class.objects.filter(
            state=self.object_class.NEW).earliest('last_update_time')
        object_found_message = 'Starting %s processor. State is "%s".' % \
                               (self.object_prefix.lower(), self.report_or_slice.state)
        LOG.info(format_message(
                    self.prefix, object_found_message,
                    account_number=self.account_number, report_id=self.report_id))
        self.transition_to_started()

    @transaction.atomic
    def assign_object(self):
        """Assign the object processor objects that are saved in the db.

        First priority is the oldest object in any state. We check to see if an 
        appropriate amount of time has passed before we retry this object.

        If none of the above qualify, we look for the oldest objects that are in the new state.
        """
        self.prefix = 'ASSIGNING %s' % self.object_prefix
        if self.report_or_slice is None:
            try:
                assign, oldest_object = self.get_oldest_object_retry()
                if assign:
                    self.report_or_slice = oldest_object
                    self.next_state = oldest_object.state
                    object_found_message = 'Starting %s processor. State is "%s".' % \
                                           (self.object_prefix.lower(), self.report_or_slice.state)
                    LOG.info(format_message(
                        self.prefix, object_found_message,
                        account_number=self.account_number, report_id=self.report_id))
                    self.update_object_state(retry=RETRY.keep_same)
                else:
                    # else we want to raise an exception to look for objects in the
                    # new state
                    raise QPCReportException()
            except (Report.DoesNotExist, ReportSlice.DoesNotExist,
                    QPCReportException):
                try:
                    self.get_new_record()
                except (Report.DoesNotExist, ReportSlice.DoesNotExist):
                    object_not_found_message = \
                        'No %s to be processed at this time. '\
                        'Checking again in %s seconds.' % (self.object_prefix.lower() + 's', str(EMPTY_QUEUE_SLEEP))
                    LOG.info(format_message(self.prefix, object_not_found_message))

    async def delegate_state(self):
        """Call the correct function based on report state.

        If the function is async, make sure to await it.
        """
        print('inside of delegate state.')
        self.pre_delegate()
        print('after calling pre-delegate')
        # if the function is async, we must await it
        if self.state_functions.get(self.state):
            if self.state in self.async_states:
                await self.state_functions.get(self.state)()
            else:
                self.state_functions.get(self.state)()
        else: 
            self.reset_variables()

    def update_object_state(self, retry=RETRY.clear,   # noqa: C901 (too-complex)
                            retry_type=None, report_json=None,
                            report_id=None, candidate_hosts=None,
                            failed_hosts=None, status=None,
                            qpc_server_version=None, qpc_server_id=None,
                            report_version=None):
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
            self.report_or_slice.last_update_time = datetime.now(pytz.utc)
            self.report_or_slice.state = self.next_state
            self.report_or_slice.git_commit = status_info.git_commit
            if not retry_type: 
                retry_type = self.object_class.TIME
            if retry == RETRY.clear:
                # reset the count to 0 (default behavior)
                self.report_or_slice.retry_count = 0
                self.report_or_slice.retry_type = self.object_class.TIME
            elif retry == RETRY.increment:
                self.report_or_slice.retry_count += 1
                self.report_or_slice.retry_type = retry_type
            # the other choice for retry is RETRY.keep_same in which case we don't
            # want to do anything to the retry count bc we want to preserve as is
            if report_json:
                self.report_or_slice.report_json = json.dumps(report_json)
            if report_id:
                self.report_or_slice.report_platform_id = report_id
            if candidate_hosts is not None:
                # candidate_hosts will get smaller and smaller until it hopefully
                # is empty because we have taken care of all ofthe candidates so
                # we rewrite this each time
                self.report_or_slice.candidate_hosts = json.dumps(candidate_hosts)
            if failed_hosts:
                # for failed hosts this list can keep growing, so we add the
                # newly failed hosts to the previous value
                failed = json.loads(self.report_or_slice.failed_hosts)
                for host in failed_hosts:
                    failed.append(host)
                self.report_or_slice.failed_hosts = json.dumps(failed)
            if status:
                self.report_or_slice.upload_ack_status = status
            if qpc_server_version:
                self.report_or_slice.qpc_server_version = qpc_server_version
            if qpc_server_id: 
                self.report_or_slice.qpc_server_id = qpc_server_id
            if report_version:
                self.report_or_slice.report_version = report_version
            state_info = json.loads(self.report_or_slice.state_info)
            state_info.append(self.next_state)
            self.report_or_slice.state_info = json.dumps(state_info)
            self.report_or_slice.save()
        except Exception as error:
            LOG.error(format_message(
                self.prefix,
                'Could not update %s record due to the following error %s.' % (
                    self.object_prefix.lower(), str(error)),
                account_number=self.account_number, report_id=self.report_id))

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
                'This %s has reached the retry limit of %s.' % (self.object_prefix.lower(), str(RETRIES_ALLOWED)),
                account_number=self.account_number, report_id=self.report_id))
            self.next_state = fail_state
            candidates = None
            failed = None
            if self.candidate_hosts:
                self.move_candidates_to_failed()
                candidates = self.candidate_hosts
                failed = self.failed_hosts
            self.update_object_state(retry=RETRY.increment, retry_type=retry_type,
                                     candidate_hosts=candidates,
                                     failed_hosts=failed)
        else:
            self.next_state = current_state
            if retry_type == self.object_class.GIT_COMMIT:
                COMMIT_RETRIES.inc()
                log_message = \
                    'Saving the %s to retry when a new commit '\
                    'is pushed. Retries: %s' % (self.object_prefix.lower(), str(self.report_or_slice.retry_count + 1))
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
                account_number=self.account_number, report_id=self.report_id))

            self.update_object_state(retry=RETRY.increment,
                                     retry_type=retry_type,
                                     candidate_hosts=candidate_hosts)
            self.reset_variables()

    def _validate_report_details(self):  # pylint: disable=too-many-locals
        """
        Verify that the report contents are a valid Insights report.

        :returns: tuple contain list of valid and invalid hosts
        """
        self.prefix = 'VALIDATE REPORT STRUCTURE'
        required_keys = ['report_platform_id',
                         'report_slice_id',
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
