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
import copy
import json
import logging
import re
import threading
from uuid import UUID

from aiokafka import AIOKafkaProducer
from kafka.errors import KafkaConnectionError, MessageSizeTooLargeError
from processor.abstract_processor import (AbstractProcessor, FAILED_TO_VALIDATE)
from processor.processor_utils import (PROCESSOR_INSTANCES,
                                       SLICE_PROCESSING_LOOP,
                                       format_message,
                                       print_error_loop_event)
from processor.report_consumer import (KAFKA_ERRORS,
                                       KafkaMsgHandlerError,
                                       QPCReportException)

from api.models import ReportSlice
from api.serializers import ReportSliceSerializer
from config.settings.base import (HOSTS_TRANSFORMATION_ENABLED,
                                  HOSTS_UPLOAD_FUTURES_COUNT,
                                  HOSTS_UPLOAD_TIMEOUT,
                                  INSIGHTS_KAFKA_ADDRESS,
                                  KAFKA_PRODUCER_OVERRIDE_MAX_REQUEST_SIZE,
                                  RETRIES_ALLOWED,
                                  RETRY_TIME,
                                  UPLOAD_TOPIC,
                                  kafka_ssl_config)

LOG = logging.getLogger(__name__)

HOSTS_UPLOAD_FUTURES_COUNT = int(HOSTS_UPLOAD_FUTURES_COUNT)
HOSTS_UPLOAD_TIMEOUT = int(HOSTS_UPLOAD_TIMEOUT)
FAILED_UPLOAD = 'UPLOAD'
RETRIES_ALLOWED = int(RETRIES_ALLOWED)
RETRY_TIME = int(RETRY_TIME)
OS_RELEASE_PATTERN = re.compile(
    r'(?P<name>[a-zA-Z\s]*)?\s*((?P<major>\d*)(\.?(?P<minor>\d*)(\.?(?P<patch>\d*))?)?)\s*'
    r'(\((?P<code>\S*)\))?'
)
OS_VS_ENUM = {'Red Hat': 'RHEL', 'CentOS': 'CentOS'}
PROCESSOR_NAME = 'report_slice_processor'
TRANSFORMED_DICT = dict({'removed': [], 'modified': [], 'missing_data': []})
NETWORK_INTERFACES_TOKENS_TO_OMIT = ['cali']


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
        self.processor_name = PROCESSOR_NAME
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
        kafka_ssl = kafka_ssl_config()
        self.producer = AIOKafkaProducer(
            loop=SLICE_PROCESSING_LOOP, bootstrap_servers=INSIGHTS_KAFKA_ADDRESS,
            max_request_size=KAFKA_PRODUCER_OVERRIDE_MAX_REQUEST_SIZE,
            security_protocol=kafka_ssl.get('security_protocol', 'PLAINTEXT'),
            ssl_context=kafka_ssl.get('ssl_context', None),
            sasl_mechanism=kafka_ssl.get('sasl_mechanism', 'PLAIN'),
            sasl_plain_username=kafka_ssl.get('sasl_plain_username', None),
            sasl_plain_password=kafka_ssl.get('sasl_plain_password', None)
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
        self.org_id = self.report_or_slice.org_id
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
            account_number=self.account_number, org_id=self.org_id,
            report_platform_id=self.report_platform_id))
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
            'Uploading hosts to inventory. State is "%s".' %
            (self.report_or_slice.state),
            account_number=self.account_number, org_id=self.org_id,
            report_platform_id=self.report_platform_id))
        request_id = None
        if self.report_or_slice.report:
            request_id = self.report_or_slice.report.request_id
        try:
            if self.candidate_hosts:
                candidates = self.generate_upload_candidates()
                await self._upload_to_host_inventory_via_kafka(candidates)
                LOG.info(
                    format_message(
                        self.prefix,
                        'All hosts were successfully uploaded (request_id:%s).'
                        % request_id,
                        account_number=self.account_number,
                        org_id=self.org_id,
                        report_platform_id=self.report_platform_id))
                self.next_state = ReportSlice.HOSTS_UPLOADED
                options = {'candidate_hosts': [], 'ready_to_archive': True}
                self.update_object_state(options=options)
            else:
                # need to not upload, but archive bc no hosts were valid
                LOG.info(
                    format_message(
                        self.prefix,
                        'There are no valid hosts to upload (request_id:%s)'
                        % request_id,
                        account_number=self.account_number,
                        org_id=self.org_id,
                        report_platform_id=self.report_platform_id))
                self.next_state = ReportSlice.FAILED_VALIDATION
                options = {'ready_to_archive': True}
                self.update_object_state(options=options)
                self.archive_report_and_slices()
        except Exception as error:  # pylint: disable=broad-except
            LOG.error(format_message(self.prefix, 'The following error occurred: %s.' % str(error),
                                     account_number=self.account_number,
                                     org_id=self.org_id,
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

    @staticmethod
    def _transform_tags(
            host: dict, transformed_obj=copy.deepcopy(TRANSFORMED_DICT)):
        """Convert tag's value into string."""
        tags = host.get('tags')
        if tags is None:
            return [host, transformed_obj]

        tags_modified = False
        for tag in tags:
            if tag['value'] is None or isinstance(tag['value'], str):
                continue

            if tag['value'] is True:
                tag['value'] = 'true'
            elif tag['value'] is False:
                tag['value'] = 'false'
            else:
                tag['value'] = str(tag['value'])

            tags_modified = True

        if tags_modified:
            transformed_obj['modified'].append('tags')

        host['tags'] = tags
        return [host, transformed_obj]

    @staticmethod
    def _remove_display_name(
            host: dict, transformed_obj=copy.deepcopy(TRANSFORMED_DICT)):
        """Remove 'display_name' field."""
        display_name = host.get('display_name')
        if display_name is None:
            return [host, transformed_obj]

        del host['display_name']
        transformed_obj['removed'].append('display_name')
        return [host, transformed_obj]

    @staticmethod
    def _remove_empty_ip_addresses(
            host: dict, transformed_obj=copy.deepcopy(TRANSFORMED_DICT)):
        """Remove 'ip_addresses' field."""
        ip_addresses = host.get('ip_addresses')
        if ip_addresses is None or ip_addresses:
            return [host, transformed_obj]

        del host['ip_addresses']
        transformed_obj['removed'].append('empty ip_addresses')
        return [host, transformed_obj]

    @staticmethod
    def _transform_mac_addresses(
            host: dict, transformed_obj=copy.deepcopy(TRANSFORMED_DICT)):
        """Make values unique and remove empty 'mac_addresses' field."""
        mac_addresses = host.get('mac_addresses')
        if mac_addresses is None:
            return [host, transformed_obj]
        if mac_addresses:
            host['mac_addresses'] = list(set(mac_addresses))
            transformed_obj['modified'].append(
                'transformed mac_addresses to store unique values')
            return [host, transformed_obj]
        del host['mac_addresses']
        transformed_obj['removed'].append('empty mac_addresses')
        return [host, transformed_obj]

    @staticmethod
    def is_valid_uuid(uuid):
        """Validate a UUID string."""
        try:
            uuid_obj = UUID(str(uuid))
        except ValueError:
            return False

        return str(uuid_obj) == uuid.lower()

    def _remove_invalid_bios_uuid(
            self, host, transformed_obj=copy.deepcopy(TRANSFORMED_DICT)):
        """Remove invalid bios UUID."""
        uuid = host.get('bios_uuid')
        if uuid is None:
            return [host, transformed_obj]

        if not self.is_valid_uuid(uuid):
            transformed_obj['removed'].append('invalid uuid: %s' % uuid)
            del host['bios_uuid']

        return [host, transformed_obj]

    @staticmethod
    def _match_regex_and_find_os_details(os_release):
        """Match Regex with os_release and return os_details."""
        source_os_release = os_release.strip()
        if not source_os_release:
            return None

        match_result = OS_RELEASE_PATTERN.match(source_os_release)
        os_details = match_result.groupdict()
        if os_details['minor']:
            os_details['version'] = f"{os_details['major']}.{os_details['minor']}"
        else:
            os_details['version'] = os_details['major']
            os_details['minor'] = '0'

        return os_details

    def _transform_os_release(
            self, host: dict, transformed_obj=copy.deepcopy(TRANSFORMED_DICT)):
        """Transform 'system_profile.os_release' label."""
        system_profile = host.get('system_profile', {})
        os_release = system_profile.get('os_release')
        if not isinstance(os_release, str):
            return [host, transformed_obj]

        os_details = self._match_regex_and_find_os_details(os_release)

        LOG.info(
            format_message(
                self.prefix,
                "os version after parsing os_release: '%s'"
                % os_details,
                account_number=self.account_number,
                org_id=self.org_id,
                report_platform_id=self.report_platform_id))

        if not os_details or not os_details['major']:
            del host['system_profile']['os_release']
            transformed_obj['removed'].append('empty os_release')
            return [host, transformed_obj]

        host['system_profile']['os_release'] = os_details['version']

        os_enum = next((
            value for key, value in OS_VS_ENUM.items()
            if key.lower() in os_details['name'].lower()), None)
        if os_enum:
            host['system_profile']['operating_system'] = {
                'major': os_details['major'],
                'minor': os_details['minor'],
                'name': os_enum
            }
        else:
            transformed_obj['missing_data'].append(
                "operating system info for os release '%s'" % os_release
            )

        if os_release == os_details['version']:
            return [host, transformed_obj]

        transformed_obj['modified'].append(
            "os_release from '%s' to '%s'" %
            (os_release, os_details['version']))

        return [host, transformed_obj]

    @staticmethod
    def _transform_os_kernel_version(
            host: dict, transformed_obj=copy.deepcopy(TRANSFORMED_DICT)):
        """Transform 'system_profile.os_kernel_version' label."""
        system_profile = host.get('system_profile', {})
        os_kernel_version = system_profile.get('os_kernel_version')

        if not isinstance(os_kernel_version, str):
            return [host, transformed_obj]

        version_value = os_kernel_version.split('-')[0]
        host['system_profile']['os_kernel_version'] = version_value
        transformed_obj['modified'].append(
            "os_kernel_version from '%s' to '%s'"
            % (os_kernel_version, version_value))

        return [host, transformed_obj]

    @staticmethod
    def _remove_mac_addresses_for_omitted_nics(
            host: dict, mac_addresses_to_omit: list,
            transformed_obj=copy.deepcopy(TRANSFORMED_DICT)):
        """Remove mac_addresses for omitted nics."""
        mac_addresses = host.get('mac_addresses')
        len_of_mac_addrs_to_omit = len(mac_addresses_to_omit)
        if not mac_addresses or len_of_mac_addrs_to_omit == 0:
            return [host, transformed_obj]

        host['mac_addresses'] = list(
            set(mac_addresses) - set(mac_addresses_to_omit))
        transformed_obj['removed'].append(
            'omit mac_addresses for omitted nics')
        return [host, transformed_obj]

    def _transform_network_interfaces(
            self, host: dict, transformed_obj=copy.deepcopy(TRANSFORMED_DICT)):
        """Transform 'system_profile.network_interfaces[]."""
        system_profile = host.get('system_profile', {})
        network_interfaces = system_profile.get('network_interfaces')

        if not network_interfaces:
            return [host, transformed_obj]

        mac_addresses_to_omit = []
        filtered_nics = []
        for nic in network_interfaces:
            if nic.get('name'):
                lowercase_name = nic['name'].lower()
                if any(map(lowercase_name.startswith,
                           NETWORK_INTERFACES_TOKENS_TO_OMIT)):
                    mac_addresses_to_omit.append(nic.get('mac_address'))
                    continue
                filtered_nics.append(nic)
        host, transformed_obj = self._remove_mac_addresses_for_omitted_nics(
            host, mac_addresses_to_omit, transformed_obj)
        increment_counts = {
            'mtu': 0,
            'ipv6_addresses': 0
        }
        filtered_nics = list({nic['name']: nic for nic in filtered_nics}.values())
        for nic in filtered_nics:
            increment_counts, nic = self._transform_mtu(
                nic, increment_counts)
            increment_counts, nic = self._transform_ipv6(
                nic, increment_counts)

        modified_fields = [
            field for field, count in increment_counts.items() if count > 0
        ]
        if len(modified_fields) > 0:
            transformed_obj['modified'].extend(modified_fields)

        host['system_profile']['network_interfaces'] = filtered_nics
        return [host, transformed_obj]

    @staticmethod
    def _transform_ipv6(nic: dict, increment_counts: dict):
        """Remove empty 'network_interfaces[]['ipv6_addresses']."""
        old_len = len(nic['ipv6_addresses'])
        nic['ipv6_addresses'] = list(
            filter(lambda ipv6: ipv6, nic['ipv6_addresses'])
        )
        new_len = len(nic['ipv6_addresses'])
        if old_len != new_len:
            increment_counts['ipv6_addresses'] += 1

        return increment_counts, nic

    @staticmethod
    def _transform_mtu(nic: dict, increment_counts: dict):
        """Transform 'system_profile.network_interfaces[]['mtu'] to Integer."""
        if (
                'mtu' not in nic or not nic['mtu'] or isinstance(
                    nic['mtu'], int)
        ):
            return increment_counts, nic
        nic['mtu'] = int(nic['mtu'])
        increment_counts['mtu'] += 1
        return increment_counts, nic

    @staticmethod
    def _remove_installed_packages(
            host: dict, transformed_obj=copy.deepcopy(TRANSFORMED_DICT)):
        """Delete installed_packages.

        Kafka message exceeds the maximum request size.
        """
        if 'installed_packages' in host['system_profile']:
            del host['system_profile']['installed_packages']
            host['tags'].append({
                'namespace': 'report_slice_preprocessor',
                'key': 'package_list_truncated',
                'value': 'True'})
            transformed_obj['removed'].append('installed_packages')

        return [host, transformed_obj]

    def _transform_single_host(self, request_id, host_id, host: dict):
        """Transform 'system_profile' fields."""
        transformed_obj = copy.deepcopy(TRANSFORMED_DICT)
        if 'system_profile' in host:
            host, transformed_obj = self._transform_os_release(
                host, transformed_obj)
            host, transformed_obj = self._transform_os_kernel_version(
                host, transformed_obj)
            host, transformed_obj = self._transform_network_interfaces(
                host, transformed_obj)

        host, transformed_obj = self._remove_empty_ip_addresses(
            host, transformed_obj)
        host, transformed_obj = self._transform_mac_addresses(
            host, transformed_obj)
        host, transformed_obj = self._remove_display_name(
            host, transformed_obj)
        host, transformed_obj = self._remove_invalid_bios_uuid(
            host, transformed_obj)
        host, transformed_obj = self._transform_tags(host, transformed_obj)
        host_request_size = bytes(json.dumps(host), 'utf-8')
        if len(host_request_size) >= KAFKA_PRODUCER_OVERRIDE_MAX_REQUEST_SIZE:
            host, transformed_obj = self._remove_installed_packages(
                host, transformed_obj)

        self._print_transformed_info(request_id, host_id, transformed_obj)
        return host

    def _print_transformed_info(self, request_id, host_id, transformed_obj):
        """Print transformed logs."""
        if transformed_obj is None:
            return

        log_sections = []
        for key, value in transformed_obj.items():
            if value:
                log_sections.append('%s: %s' % (key, (',').join(value)))

        if log_sections:
            log_message = (
                'Transformed details host with id %s (request_id: %s):\n'
                % (host_id, request_id)
            )
            log_message += '\n'.join(log_sections)
            LOG.info(
                format_message(
                    self.prefix,
                    log_message,
                    account_number=self.account_number,
                    org_id=self.org_id,
                    report_platform_id=self.report_platform_id
                )
            )

    # pylint:disable=too-many-locals
    # pylint: disable=too-many-statements
    # pylint: disable=too-many-branches
    @KAFKA_ERRORS.count_exceptions()  # noqa: C901 (too-complex)
    async def _upload_to_host_inventory_via_kafka(self, hosts):
        """
        Upload to the host inventory via kafka.

        :param: hosts <list> the hosts to upload.
        """
        self.prefix = 'UPLOAD TO INVENTORY VIA KAFKA'
        await self.producer.stop()
        self.producer = AIOKafkaProducer(
            loop=SLICE_PROCESSING_LOOP, bootstrap_servers=INSIGHTS_KAFKA_ADDRESS,
            max_request_size=KAFKA_PRODUCER_OVERRIDE_MAX_REQUEST_SIZE
        )
        try:
            await self.producer.start()
        except (KafkaConnectionError, TimeoutError):
            KAFKA_ERRORS.inc()
            self.should_run = False
            print_error_loop_event()
            raise KafkaMsgHandlerError(
                format_message(
                    self.prefix,
                    'Unable to connect to kafka server.',
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id))
        total_hosts = len(hosts)
        count = 0
        send_futures = []
        associated_msg = []
        report = self.report_or_slice.report
        cert_cn = None
        try:
            b64_identity = json.loads(report.upload_srv_kafka_msg)['b64_identity']
            raw_b64_identity = base64.b64decode(b64_identity).decode('utf-8')
            identity = json.loads(raw_b64_identity)
            cert_cn = identity['identity']['system']['cn']
        except KeyError as err:
            LOG.error(format_message(
                self.prefix, 'Invalid identity. Key not found: %s' % err))

        unique_id_base = '{}:{}:{}:'.format(report.request_id,
                                            report.report_platform_id,
                                            self.report_or_slice.report_slice_id)
        try:  # pylint: disable=too-many-nested-blocks
            for host_id, host in hosts.items():
                if HOSTS_TRANSFORMATION_ENABLED:
                    host = self._transform_single_host(
                        report.request_id, host_id, host)
                    if cert_cn and ('system_profile' in host):
                        host['system_profile']['owner_id'] = cert_cn
                system_unique_id = unique_id_base + host_id
                count += 1
                if not host.get('org_id'):
                    continue
                upload_msg = {
                    'operation': 'add_host',
                    'data': host,
                    'platform_metadata': {'request_id': system_unique_id,
                                          'b64_identity': b64_identity}
                }
                msg = bytes(json.dumps(upload_msg), 'utf-8')
                try:
                    future = await self.producer.send(UPLOAD_TOPIC, msg)
                    send_futures.append(future)
                    associated_msg.append(upload_msg)
                except MessageSizeTooLargeError as err:
                    LOG.error('Unable to upload the host %s with org_id %s: %s',
                              host.get('fqdn'), host.get('org_id'), err)
                if count % HOSTS_UPLOAD_FUTURES_COUNT == 0 or count == total_hosts:
                    LOG.info(
                        format_message(
                            self.prefix,
                            'Sending %s/%s hosts to the inventory service.' % (count, total_hosts),
                            account_number=self.account_number,
                            org_id=self.org_id,
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
            print_error_loop_event()
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
                                         name=PROCESSOR_NAME,
                                         args=(SLICE_PROCESSING_LOOP,))
    event_loop_thread.daemon = True
    event_loop_thread.start()
