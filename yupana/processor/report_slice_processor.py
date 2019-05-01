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
import base64
import json
import logging
import math
import tarfile
import threading
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

from processor.abstract_processor import (AbstractProcessor, COMMIT_RETRIES, 
                                          TIME_RETRIES, INVALID_HOSTS,
                                          ARCHIVED_FAIL, ARCHIVED_SUCCESS,
                                          FAILED_TO_DOWNLOAD, FAILED_TO_VALIDATE,
                                          INVALID_REPORTS, VALIDATION_LATENCY,
                                          UPLOAD_GROUP_SIZE, HOST_UPLOAD_REQUEST_LATENCY,
                                          HOSTS_UPLOADED_FAILED, HOSTS_UPLOADED_SUCCESS,
                                          VALID_HOSTS)

from prometheus_client import Counter, Gauge, Summary

from api.models import (Report, ReportArchive, 
                        ReportSlice, ReportSliceArchive, 
                        Status)
from config.settings.base import (INSIGHTS_HOST_INVENTORY_URL,
                                  INSIGHTS_KAFKA_ADDRESS,
                                  RETRIES_ALLOWED,
                                  RETRY_TIME)

LOG = logging.getLogger(__name__)
SLICE_PROCESSING_LOOP = asyncio.new_event_loop()
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


class RetryUploadTimeException(Exception):
    """Use to report upload errors that should be retried on time."""

    pass


class RetryUploadCommitException(Exception):
    """Use to report upload errors that should be retried on commit."""

    pass


class ReportSliceProcessor(AbstractProcessor):
    """Class for processing report slices that have been created."""

    def __init__(self):
        """Create a report slice state machine."""
        self.state_functions = {
            ReportSlice.RETRY_VALIDATION: self.transition_to_new,
            ReportSlice.NEW: self.transition_to_started,
            ReportSlice.STARTED: self.transition_to_hosts_uploaded,
            ReportSlice.VALIDATED: self.transition_to_hosts_uploaded,
            ReportSlice.HOSTS_UPLOADED: self.archive_report,
            ReportSlice.FAILED_VALIDATION: self.archive_report,
            ReportSlice.FAILED_HOSTS_UPLOAD: self.archive_report}
        super().__init__(pre_delegate=self.pre_delegate,
                         state_functions=self.state_functions,
                         async_states=[],
                         object_prefix='REPORT SLICE',
                         object_class=ReportSlice
                         )

    def pre_delegate(self):
        """Call the correct function based on report slice state.

        If the function is async, make sure to await it.
        """
        self.state = self.report_or_slice.state
        self.account_number = self.report_or_slice.rh_account
        if self.report_or_slice.candidate_hosts:
            self.candidate_hosts = json.loads(self.report_or_slice.candidate_hosts)
        if self.report_or_slice.failed_hosts:
            self.failed_hosts = json.loads(self.report_or_slice.failed_hosts)
        if self.report_or_slice.report_json:
            self.report_json = json.loads(self.report_or_slice.report_json)
        if self.report_or_slice.report_platform_id:
            self.report_id = self.report_or_slice.report_platform_id

    def transition_to_new(self):
        """The slice is in the failed validation state so we need to revalidate it."""
        self.prefix = 'ATTEMPTING VALIDATION'
        LOG.info(format_message(
            self.prefix, 'Uploading hosts to inventory. State is "%s".' % self.report_or_slice.state,
            account_number=self.account_number, report_id=self.report_id))
        try: 
            self.report_json = json.loads(self.report_or_slice.report_json)
            self.candidate_hosts, self.failed_hosts = self._validate_report_details()
            INVALID_HOSTS.set(len(self.failed_hosts))
            # Here we want to update the report state of the actual report slice & when finished, of the report
            self.next_state = ReportSlice.NEW
            self.update_object_state(candidate_hosts=self.candidate_hosts, failed_hosts=self.failed_hosts)
        except QPCReportException:
            # if any QPCReportExceptions occur, we know that the report is not valid but has been
            # successfully validated
            # that means that this slice is invalid and only awaits being archived 
            self.next_state = ReportSlice.FAILED_VALIDATION
            self.update_object_state()
        except Exception as error:
            # This slice blew up validation - we want to retry it later, which means it enters our odd state 
            # of requiring validation 
            LOG.error(format_message(self.prefix, 'The following error occurred: %s.' % str(error)))
            self.determine_retry(ReporSlice.FAILED_VALIDATION, ReportSlice.RETRY_VALIDATION, 
                                 retry_type=ReportSlice.GIT_COMMIT)

    def transition_to_hosts_uploaded(self):
        """Upload the host candidates to inventory & move to hosts_uploaded state."""
        self.prefix = 'ATTEMPTING HOST UPLOAD'
        LOG.info(format_message(
            self.prefix, 'Uploading hosts to inventory. State is "%s".' % self.report_or_slice.state,
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
                    self.next_state = ReportSlice.HOSTS_UPLOADED
                    self.update_report_state(candidate_hosts=[])
                else:
                    candidates = []
                    # if both retry_commit_candidates and retry_time_candidates are returned
                    # (ie. we got both 400 & 500 status codes were returned), we give the
                    # retry_time precedence because we want to retry those with the hope that
                    # they will succeed and leave behind the retry_commit hosts
                    if retry_commit_candidates:
                        candidates += retry_commit_candidates
                        retry_type = ReportSlice.GIT_COMMIT
                    if retry_time_candidates:
                        candidates += retry_time_candidates
                        retry_type = ReportSlice.TIME
                    LOG.info(format_message(self.prefix, 'Hosts were not successfully uploaded',
                                            account_number=self.account_number,
                                            report_id=self.report_id))
                    self.determine_retry(ReportSlice.FAILED_HOSTS_UPLOAD,
                                         ReportSlice.VALIDATED,
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
            self.determine_retry(ReportSlice.FAILED_HOSTS_UPLOAD, ReportSlice.VALIDATED,
                                 retry_type=ReportSlice.GIT_COMMIT)

    def record_failed_state_metrics(self):
        """Record the metrics based on the report state."""
        state_to_metric = {
            ReportSlice.FAILED_DOWNLOAD: FAILED_TO_DOWNLOAD.inc,
            ReportSlice.FAILED_VALIDATION: FAILED_TO_VALIDATE.inc
        }
        if self.state in state_to_metric.keys():
            state_to_metric.get(self.state)()

    @transaction.atomic
    def archive_report(self):
        """Archive the report slice object."""
        self.prefix = 'ARCHIVING REPORT'
        failed = False
        LOG.info(format_message(self.prefix, 'Archiving report.',
                                account_number=self.account_number, report_id=self.report_id))
        archived = ReportSliceArchive(
            rh_account=self.account_number,
            retry_count=self.report_or_slice.retry_count,
            retry_type=self.report_or_slice.retry_type,
            candidate_hosts=self.report_or_slice.candidate_hosts,
            failed_hosts=self.report_or_slice.failed_hosts,
            state=self.state,
            state_info=self.report_or_slice.state_info,
            last_update_time=self.report_or_slice.last_update_time,
        )
        if self.report_id:
            archived.report_platform_id = self.report_id
        if self.report_json:
            archived.report_json = self.report_json
        archived.save()

        failed_states = [ReportSlice.FAILED_VALIDATION, ReportSlice.FAILED_HOSTS_UPLOAD]
        if self.state in failed_states or failed:
            ARCHIVED_FAIL.inc()
        else:
            print('Increment success archive')
            ARCHIVED_SUCCESS.inc()
        self.record_failed_state_metrics()
        try:
            ReportSlice.objects.get(id=self.report_or_slice.id).delete()
        except ReportSlice.DoesNotExist:
            pass
        LOG.info(format_message(self.prefix, 'Report slice successfully archived.',
                                account_number=self.account_number, report_id=self.report_id))
        self.reset_variables()


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
            UPLOAD_GROUP_SIZE.set(len(hosts_list))
            group_count += 1
            LOG.info(format_message(
                self.prefix,
                'Uploading hosts group %s/%s. Group size: %s hosts' %
                (group_count, len(hosts_lists_to_upload), len(hosts_list)),
                account_number=self.account_number, report_id=self.report_id))
            try:  # pylint: disable=too-many-nested-blocks
                with HOST_UPLOAD_REQUEST_LATENCY.time():
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
    processor = ReportSliceProcessor()
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