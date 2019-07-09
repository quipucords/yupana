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
"""Legacy Report Slice Processor."""

import asyncio
import base64
import concurrent.futures
import json
import logging
import threading
from datetime import datetime
from http import HTTPStatus

import pytz
import requests
from processor.legacy_abstract_processor import (
    FAILED_TO_VALIDATE,
    HOSTS_UPLOADED_FAILED, HOSTS_UPLOADED_SUCCESS,
    HOST_UPLOAD_REQUEST_LATENCY, INVALID_HOSTS,
    LegacyAbstractProcessor,
    UPLOAD_GROUP_SIZE, VALID_HOSTS)
from processor.legacy_report_consumer import (
    QPCReportException,
    format_message)

from api.models import InventoryUploadError, LegacyReportSlice
from api.serializers import InventoryUploadErrorSerializer, LegacyReportSliceSerializer
from config.settings.base import (HOSTS_PER_REQ,
                                  HOST_INVENTORY_UPLOAD_MODE,
                                  INSIGHTS_HOST_INVENTORY_URL,
                                  MAX_THREADS,
                                  RETRIES_ALLOWED,
                                  RETRY_TIME)

LOG = logging.getLogger(__name__)
SLICE_PROCESSING_LOOP = asyncio.new_event_loop()

FAILED_UPLOAD = 'UPLOAD'
RETRIES_ALLOWED = int(RETRIES_ALLOWED)
RETRY_TIME = int(RETRY_TIME)
HOSTS_PER_REQ = int(HOSTS_PER_REQ)
MAX_THREADS = int(MAX_THREADS)
INVENTORY_FAILURE = 'INVENTORY FAILURE'
UPLOAD_DATA_FAILURE = 'UPLOAD DATA FAILURE'


class RetryUploadTimeException(Exception):
    """Use to report upload errors that should be retried on time."""

    pass


class RetryUploadCommitException(Exception):
    """Use to report upload errors that should be retried on commit."""

    pass


class LegacyReportSliceProcessor(LegacyAbstractProcessor):  # pylint: disable=too-many-instance-attributes
    """Class for processing report slices that have been created."""

    def __init__(self):
        """Create a report slice state machine."""
        state_functions = {
            LegacyReportSlice.RETRY_VALIDATION: self.transition_to_validated,
            LegacyReportSlice.NEW: self.transition_to_started,
            LegacyReportSlice.STARTED: self.transition_to_hosts_uploaded,
            LegacyReportSlice.VALIDATED: self.transition_to_hosts_uploaded,
            LegacyReportSlice.HOSTS_UPLOADED: self.archive_report_and_slices,
            LegacyReportSlice.FAILED_VALIDATION: self.archive_report_and_slices,
            LegacyReportSlice.FAILED_HOSTS_UPLOAD: self.archive_report_and_slices}
        state_metrics = {
            LegacyReportSlice.FAILED_VALIDATION: FAILED_TO_VALIDATE.inc
        }
        super().__init__(pre_delegate=self.pre_delegate,
                         state_functions=state_functions,
                         state_metrics=state_metrics,
                         async_states=[LegacyReportSlice.STARTED, LegacyReportSlice.VALIDATED],
                         object_prefix='REPORT SLICE',
                         object_class=LegacyReportSlice,
                         object_serializer=LegacyReportSliceSerializer
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
            self.candidate_hosts, self.failed_hosts = self._validate_report_details()
            INVALID_HOSTS.set(len(self.failed_hosts))
            # Here we want to update the report state of the actual report slice & when finished
            self.next_state = LegacyReportSlice.VALIDATED
            options = {'candidate_hosts': self.candidate_hosts,
                       'failed_hosts': self.failed_hosts}
            self.update_object_state(options=options)
        except QPCReportException:
            # if any QPCReportExceptions occur, we know that the report is not valid but has been
            # successfully validated
            # that means that this slice is invalid and only awaits being archived
            self.next_state = LegacyReportSlice.FAILED_VALIDATION
            self.update_object_state(options={})
        except Exception as error:  # pylint: disable=broad-except
            # This slice blew up validation - we want to retry it later,
            # which means it enters our odd state
            # of requiring validation
            LOG.error(format_message(self.prefix, 'The following error occurred: %s.' % str(error)))
            self.determine_retry(LegacyReportSlice.FAILED_VALIDATION,
                                 LegacyReportSlice.RETRY_VALIDATION,
                                 retry_type=LegacyReportSlice.GIT_COMMIT)

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
                if HOST_INVENTORY_UPLOAD_MODE.lower() == 'http':
                    retry_time_candidates, retry_commit_candidates = \
                        await self._upload_to_host_inventory(candidates)
                else:
                    # placeholder for upload via Kafka function
                    retry_time_candidates, retry_commit_candidates = \
                        await self._upload_to_host_inventory(candidates)
                if not retry_time_candidates and not retry_commit_candidates:
                    LOG.info(format_message(self.prefix, 'All hosts were successfully uploaded.',
                                            account_number=self.account_number,
                                            report_platform_id=self.report_platform_id))
                    self.next_state = LegacyReportSlice.HOSTS_UPLOADED
                    options = {'candidate_hosts': [], 'ready_to_archive': True}
                    self.update_object_state(options=options)
                else:
                    candidates = []
                    # if both retry_commit_candidates and retry_time_candidates are returned
                    # (ie. we got both 400 & 500 status codes were returned), we give the
                    # retry_time precedence because we want to retry those with the hope that
                    # they will succeed and leave behind the retry_commit hosts
                    if retry_commit_candidates:
                        candidates += retry_commit_candidates
                        retry_type = LegacyReportSlice.GIT_COMMIT
                    if retry_time_candidates:
                        candidates += retry_time_candidates
                        retry_type = LegacyReportSlice.TIME
                    LOG.info(format_message(self.prefix, 'Hosts were not successfully uploaded',
                                            account_number=self.account_number,
                                            report_platform_id=self.report_platform_id))
                    self.determine_retry(LegacyReportSlice.FAILED_HOSTS_UPLOAD,
                                         LegacyReportSlice.VALIDATED,
                                         candidate_hosts=candidates,
                                         retry_type=retry_type)
            else:
                # need to not upload, but archive bc no hosts were valid
                LOG.info(format_message(self.prefix, 'There are no valid hosts to upload',
                                        account_number=self.account_number,
                                        report_platform_id=self.report_platform_id))
                self.next_state = LegacyReportSlice.FAILED_VALIDATION
                options = {'ready_to_archive': True}
                self.update_object_state(options=options)
                self.archive_report_and_slices()
        except Exception as error:  # pylint: disable=broad-except
            LOG.error(format_message(self.prefix, 'The following error occurred: %s.' % str(error),
                                     account_number=self.account_number,
                                     report_platform_id=self.report_platform_id))
            self.determine_retry(LegacyReportSlice.FAILED_HOSTS_UPLOAD, LegacyReportSlice.VALIDATED,
                                 retry_type=LegacyReportSlice.GIT_COMMIT)

    @staticmethod
    def generate_bulk_upload_list(hosts):  # pylint:disable=too-many-locals
        """Generate a list of hosts to upload.

        :param hosts: <dict> dictionary containing hosts to upload.
        """
        bulk_upload_list = []
        for _, host in hosts.items():
            bulk_upload_list.append(host)
        return bulk_upload_list

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

    @staticmethod
    def split_hosts(list_of_all_hosts):
        """Split up the hosts into lists of 1000 or less."""
        hosts_lists_to_upload = \
            [list_of_all_hosts[i:i + HOSTS_PER_REQ]
             for i in range(0, len(list_of_all_hosts), HOSTS_PER_REQ)]
        # hosts list to upload is a list containing lists of however many hosts can be
        # uploaded at one time (aka. 1000). We now need to break this into a list
        # of lists that have the same number of lists as we do threads
        # for example, if the max number of hosts per req is 100, and our max number of threads
        # is 3, our final list would look like this:
        # [[[100 hosts], [100 hosts], [100 hosts]], [[100 hosts], [100 hosts], [100 hosts]]]
        thread_lists_to_upload = \
            [hosts_lists_to_upload[i:i + MAX_THREADS]
             for i in range(0, len(hosts_lists_to_upload), MAX_THREADS)]
        return thread_lists_to_upload

    def record_inventory_upload_errors(self, options):
        """Record request and response body of requests that failed to upload.

        :param options: <dict> containing the source and details of the failure.
        """
        details = options.get('details')
        source = options.get('source')
        upload_type = options.get('upload_type')

        inventory_upload_error = {
            'report_platform_id': self.report_platform_id,
            'report_slice_id': self.report_slice_id,
            'account': self.account_number,
            'details': json.dumps(details),
            'source': source,
            'upload_type': upload_type
        }
        error_serializer = InventoryUploadErrorSerializer(data=inventory_upload_error)
        if error_serializer.is_valid(raise_exception=True):
            error_serializer.save()
            LOG.info(
                format_message(
                    self.prefix,
                    'Saved request & response body for hosts that failed '
                    'to upload from report slice %s' % self.report_slice_id,
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id))
        return True

    # pylint: disable=too-many-locals, too-many-nested-blocks, too-many-branches
    # pylint: disable=too-many-statements
    def execute_request(self, hosts_tuple):  # noqa: C901 (too-complex)
        """Execute the http requests for posting to inventory service."""
        hosts_list, hosts = hosts_tuple
        LOG.info('Thread %s spawned, attempting to upload %s hosts',
                 threading.current_thread().name, len(hosts_list))
        identity_string = '{"identity": {"account_number": "%s"}}' % str(self.account_number)
        bytes_string = identity_string.encode()
        x_rh_identity_value = base64.b64encode(bytes_string).decode()
        identity_header = {'x-rh-identity': x_rh_identity_value,
                           'Content-Type': 'application/json'}
        failed_hosts = []
        retry_time_candidates = []  # storing hosts to retry after time
        retry_commit_candidates = []  # storing hosts to retry after commit change
        error_messages = []
        details = {'identity_header': identity_header}
        inventory_error_info = {'upload_type': InventoryUploadError.HTTP,
                                'source': self.report_or_slice.source}
        retry_exception = False
        inventory_error = False
        UPLOAD_GROUP_SIZE.set(len(hosts_list))
        try:
            with HOST_UPLOAD_REQUEST_LATENCY.time():
                response = requests.post(INSIGHTS_HOST_INVENTORY_URL,
                                         data=json.dumps(hosts_list),
                                         headers=identity_header)
            details['response_code'] = response.status_code
            if response.status_code in [HTTPStatus.MULTI_STATUS]:
                try:
                    json_body = response.json()
                except ValueError:
                    # something went wrong
                    json_error = 'Missing JSON response.'
                    error_messages.append(json_error)
                    details['additional_info'] = json_error
                    inventory_error = True
                    details['failure_category'] = INVENTORY_FAILURE
                    raise RetryUploadTimeException()

                errors = json_body.get('errors')
                if errors != 0:
                    details['response_body'] = json_body
                    inventory_error = True
                    details['failure_category'] = UPLOAD_DATA_FAILURE
                    all_data = json_body.get('data', [])
                    host_index = 0
                    for host_data in all_data:
                        host_status = host_data.get('status')
                        if host_status not in [HTTPStatus.OK, HTTPStatus.CREATED]:
                            original_host = {}
                            host_id = ''
                            upload_host = hosts_list[host_index]
                            host_facts = upload_host.get('facts', [])
                            for namespace_facts in host_facts:
                                if namespace_facts.get('namespace') == 'yupana':
                                    yupana_facts = namespace_facts.get('facts', {})
                                    host_id = yupana_facts.get('yupana_host_id', '')
                                    original_host = hosts.get(host_id, {})
                            failed_hosts.append({
                                'status_code': host_status,
                                'display_name': original_host.get('display_name'),
                                'yupana_host_id': host_id,
                                'host': original_host})

                            # if the response code is a 500, then something on
                            # host inventory side blew up and we want to retry
                            # after a certain amount of time
                            if str(host_status).startswith('5'):
                                retry_time_candidates.append({host_id: original_host,
                                                              'cause': FAILED_UPLOAD,
                                                              'status_code': host_status})
                            else:
                                # else, if we recieved a 400 status code, the problem is
                                # likely on our side so we should retry after a code change
                                retry_commit_candidates.append({host_id: original_host,
                                                                'cause': FAILED_UPLOAD,
                                                                'status_code': host_status})
                        host_index += 1
            else:
                # something unexpected happened
                inventory_error = True
                error_messages.append(
                    'Post request recieved the following response code: %s' %
                    str(response.status_code))
                error_messages.append('Attempted to upload the following: %s' % str(hosts_list))
                try:
                    message = response.json()
                    details['response_body'] = message
                    error_messages.append(message)
                except ValueError:
                    json_error = 'Missing JSON response.'
                    details['additional_info'] = json_error
                    error_messages.append(json_error)
                    error_messages.append('Unexpected response code %s' % str(response.status_code))
                if str(response.status_code).startswith('5'):
                    details['failure_category'] = INVENTORY_FAILURE
                    # something went wrong on host inventory side and we should regenerate after
                    # some time has passed
                    raise RetryUploadTimeException()
                # else something went wrong possibly on our side (if its a 400)
                # and we should regenerate the hosts dictionary and re-upload after a commit
                details['failure_category'] = UPLOAD_DATA_FAILURE
                raise RetryUploadCommitException()

        except RetryUploadCommitException:
            retry_exception = True
            retry_list = retry_commit_candidates
        except RetryUploadTimeException:
            retry_exception = True
            retry_list = retry_time_candidates
        except requests.exceptions.RequestException as err:
            inventory_error = True
            details['failure_category'] = INVENTORY_FAILURE
            request_error = 'A request exception occurred: %s ' % str(err)
            details['additional_info'] = request_error
            error_messages.append(request_error)
            error_messages.append('Attempted to upload the following: %s' % str(hosts_list))
            retry_exception = True
            retry_list = retry_time_candidates

        if retry_exception:
            # we are going to have to look up the original host, and map it to the
            # one we are trying to upload so that we can retry it.
            for upload_host in hosts_list:
                host_id = 'unknown'
                original_host = {}
                host_facts = upload_host.get('facts', [])
                for namespace_facts in host_facts:
                    if namespace_facts.get('namespace') == 'yupana':
                        yupana_facts = namespace_facts.get('facts', {})
                        host_id = yupana_facts.get('yupana_host_id', '')
                        original_host = hosts.get(host_id, {})
                failed_hosts.append({
                    'status_code': 'unknown',
                    'display_name': original_host.get('display_name', 'unknown'),
                    'yupana_host_id': host_id,
                    'host': original_host})
                retry_list.append({host_id: original_host,
                                   'cause': FAILED_UPLOAD})

        response = {
            'retry_time_candidates': retry_time_candidates,
            'retry_commit_candidates': retry_commit_candidates,
            'failed_hosts': failed_hosts,
            'error_messages': error_messages
        }
        details['date'] = str(datetime.now(pytz.utc))
        inventory_error_info['details'] = details
        if inventory_error:
            response['inventory_error'] = inventory_error_info
        return response

    async def _upload_to_host_inventory(self, hosts):  # noqa: C901 (too-complex)
        """Create bulk upload threads for post requests to inventory."""
        self.prefix = 'UPLOAD TO HOST INVENTORY'
        failed_hosts = []
        inventory_errors = []
        all_error_messages = []
        retry_time_hosts = []  # storing hosts to retry after time
        retry_commit_hosts = []  # storing hosts to retry after commit change
        list_of_all_hosts = self.generate_bulk_upload_list(hosts)
        hosts_lists_to_upload = self.split_hosts(list_of_all_hosts)
        LOG.info(format_message(self.prefix, 'Spawning threads to upload hosts.',
                                account_number=self.account_number,
                                report_platform_id=self.report_platform_id))
        for split_list in hosts_lists_to_upload:
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
                process_loop = asyncio.get_event_loop()
                futures = [
                    process_loop.run_in_executor(
                        executor,
                        self.execute_request,
                        (hosts_list, hosts)
                    )
                    for hosts_list in split_list
                ]
                for response in await asyncio.gather(*futures):
                    retry_time_candidates = response.get('retry_time_candidates', [])
                    retry_commit_candidates = response.get('retry_commit_candidates', [])
                    failed_candidates = response.get('failed_hosts', [])
                    error_messages = response.get('error_messages', [])
                    inventory_error = response.get('inventory_error')
                    if inventory_error:
                        inventory_errors.append(inventory_error)
                    retry_time_hosts += retry_time_candidates
                    retry_commit_hosts += retry_commit_candidates
                    failed_hosts += failed_candidates
                    all_error_messages += error_messages

        for error in all_error_messages:
            LOG.error(format_message(self.prefix, error,
                                     account_number=self.account_number,
                                     report_platform_id=self.report_platform_id
                                     ))
        for inventory_error in inventory_errors:
            try:
                self.record_inventory_upload_errors(inventory_error)
            except Exception as err:  # pylint:disable=broad-except
                LOG.error(
                    format_message(
                        self.prefix,
                        'Could not save inventory error due to the '
                        'following exception: %s' % str(err),
                        account_number=self.account_number,
                        report_platform_id=self.report_platform_id))
        total_hosts_count = len(hosts)
        failed_hosts_count = len(failed_hosts)
        successful = total_hosts_count - failed_hosts_count
        VALID_HOSTS.set(total_hosts_count)
        HOSTS_UPLOADED_SUCCESS.set(successful)
        HOSTS_UPLOADED_FAILED.set(failed_hosts_count)
        upload_msg = format_message(
            self.prefix, '%s/%s hosts uploaded to host inventory' %
            (successful, len(hosts)),
            account_number=self.account_number,
            report_platform_id=self.report_platform_id
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
                    'yupana_host_id: %s. host: %s' % (
                        failed_info.get('status_code'),
                        failed_info.get('display_name'),
                        failed_info.get('yupana_host_id'),
                        failed_info.get('host')),
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id
                ))
        return retry_time_hosts, retry_commit_hosts


def asyncio_report_processor_thread(loop):  # pragma: no cover
    """
    Worker thread function to run the asyncio event loop.

    Creates a report processor and calls the run method.

    :param loop: event loop
    :returns None
    """
    processor = LegacyReportSliceProcessor()
    loop.run_until_complete(processor.run())


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
