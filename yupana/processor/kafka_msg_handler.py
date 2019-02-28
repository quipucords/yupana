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
"""Kafka message handler."""

import asyncio
import base64
import json
import logging
import tarfile
import threading
from datetime import datetime
from http import HTTPStatus
from io import BytesIO

import requests
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from kafka.errors import ConnectionError as KafkaConnectionError

from api.models import Report, ReportArchive
from config.settings.base import (INSIGHTS_HOST_INVENTORY_URL,
                                  INSIGHTS_KAFKA_ADDRESS)

LOG = logging.getLogger(__name__)
EVENT_LOOP = asyncio.get_event_loop()
PROCESSING_LOOP = asyncio.new_event_loop()
MSG_PENDING_QUEUE = asyncio.Queue()
QPC_TOPIC = 'platform.upload.qpc'
AVAILABLE_TOPIC = 'platform.upload.available'
VALIDATION_TOPIC = 'platform.upload.validation'
SUCCESS_CONFIRM_STATUS = 'success'
FAILURE_CONFIRM_STATUS = 'failure'
CANONICAL_FACTS = ['insights_client_id', 'bios_uuid', 'ip_addresses', 'mac_addresses',
                   'vm_uuid', 'etc_machine_id', 'subscription_manager_id']
# we will want to revert these to the defaults when we improve processing/ACK time in yupana
SESSION_TIMEOUT = 300 * 1000
REQUEST_TIMEOUT = 500 * 1000

RETRY_TIME = 1  # this is the time in minutes that we want to wait to retry a report
RETRIES_ALLOWED = 5  # this is the number of retries that we want to allow before failing a report


def format_message(prefix, message, account_number=None, report_id=None):
    """Format log messages in a consistent way.

    :param prefix: (str) A meaningful prefix to be displayed in all caps.
    :param message: (str) A short message describing the state
    :param account_number: (str) The account sending the report.
    :param report_id: (str) The qpc report id.
    :returns: (str) containing formatted message
    """
    if not report_id and not account_number:
        actual_message = 'Report %s - %s' % (prefix, message)
    elif account_number and not report_id:
        actual_message = 'Report(account=%s) %s - %s' % (account_number, prefix, message)
    else:
        actual_message = 'Report(account=%s, report_id=%s) %s - %s' % (account_number, report_id, prefix, message)

    return actual_message


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


def unpack_consumer_record(upload_service_message):
    """Retrieve report URL from kafka.

    :param upload_service_message: the value of the kakfa message from file
        upload service.
    :returns: str containing the url to the qpc report's tar.gz file.
    """
    prefix = 'NEW REPORT UPLOAD'
    try:
        json_message = json.loads(upload_service_message.value.decode('utf-8'))
        message = 'received on %s topic' % upload_service_message.topic
        account_number = json_message.get('rh_account')
        LOG.info(format_message(prefix,
                                message,
                                account_number=account_number))
        LOG.debug(format_message(
            prefix,
            'Message: %s' % str(upload_service_message),
            account_number=account_number))
        return json_message
    except ValueError:
        raise QPCKafkaMsgException(format_message(prefix, 'Upload service message not JSON.'))


def download_report(account_number, upload_service_message):
    """
    Download report.

    :param account_number: <str> of the User's account number.
    :param upload_service_message: the value of the kakfa message
    :returns content: The tar.gz binary content or None if there are errors.
    """
    prefix = 'REPORT DOWNLOAD'
    try:
        report_url = upload_service_message.get('url', None)
        if not report_url:
            raise QPCReportException(
                format_message(
                    prefix,
                    'kafka message missing report url.  Message: %s' % upload_service_message,
                    account_number=account_number))

        LOG.info(format_message(prefix, 'downloading %s' % report_url, account_number=account_number
                                ))
        download_response = requests.get(report_url)
        if download_response.status_code != HTTPStatus.OK:
            raise QPCKafkaMsgException(
                format_message(prefix,
                               'HTTP status code %s returned for URL %s.  Message: %s' % (
                                   download_response.status_code,
                                   report_url,
                                   upload_service_message),
                               account_number=account_number))

        LOG.info(format_message(
            prefix,
            'successfully downloaded %s' % report_url,
            account_number=account_number
        ))
        return download_response.content
    except requests.exceptions.HTTPError as err:
        raise QPCReportException(
            format_message(prefix,
                           'Unexpected http error for URL %s. Error: %s' % (
                               report_url,
                               err),
                           account_number=account_number))


def extract_report_from_tar_gz(account_number, report_tar_gz):
    """Extract Insights report from tar.gz file.

    :param account_number: the account number associated with report
    :param report_tar_gz: A hexstring or BytesIO tarball
        saved in memory with gzip compression.
    :returns: Insights report as dict
    """
    prefix = 'EXTRACT REPORT FROM TAR'
    try:
        tar = tarfile.open(fileobj=BytesIO(report_tar_gz), mode='r:gz')
        files_check = tar.getmembers()
        json_files = []
        for file in files_check:
            if '.json' in file.name:
                json_files.append(file)
        if len(json_files) > 1:
            raise QPCReportException(
                format_message(prefix,
                               'tar.gz contains multiple files.',
                               account_number=account_number))
        if json_files:
            file = json_files[0]
            tarfile_obj = tar.extractfile(file)
            report_json_str = tarfile_obj.read().decode('utf-8')
            try:
                insights_report = json.loads(report_json_str)
                LOG.info(
                    format_message(
                        prefix, 'successful',
                        account_number=account_number,
                        report_id=insights_report.get('report_platform_id')))
                return insights_report
            except ValueError as error:
                raise QPCReportException(
                    format_message(prefix,
                                   'Report not JSON. Error: %s' % str(error),
                                   account_number=account_number))
        raise QPCReportException(
            format_message(prefix,
                           'Tar contains no JSON files.',
                           account_number=account_number))
    except QPCReportException as qpc_err:
        raise qpc_err
    except Exception as err:
        raise QPCReportException(
            format_message(prefix,
                           'Unexpected error reading tar.gz: %s' % str(err),
                           account_number=account_number))


def verify_report_details(account_number, insights_report):
    """
    Verify that the report contents are a valid Insights report.

    :param account_number: the account number associated with report
    :param insights_report: dict with report
    :returns: tuple contain list of valid and invalid hosts
    """
    prefix = 'VERIFY REPORT STRUCTURE'
    required_keys = ['report_platform_id',
                     'report_id',
                     'report_version',
                     'hosts']
    report_id = insights_report.get('report_platform_id')

    if insights_report.get('report_type') != 'insights':
        raise QPCReportException(
            format_message(
                prefix,
                'Attribute report_type missing or not equal to insights',
                account_number=account_number,
                report_id=report_id))

    missing_keys = []
    for key in required_keys:
        required_key = insights_report.get(key)
        if not required_key:
            missing_keys.append(key)

    if missing_keys:
        missing_keys_str = ', '.join(missing_keys)
        raise QPCReportException(
            format_message(
                prefix,
                'Report is missing required fields: %s.' % missing_keys_str,
                account_number=account_number,
                report_id=report_id))

    # validate hosts is a dictionary
    invalid_hosts_message = 'Hosts must be a dictionary that is not empty. ' \
                            'All keys must be strings and all values must be dictionaries.',
    hosts = insights_report.get('hosts')
    if not hosts or not isinstance(hosts, dict):
        raise QPCReportException(
            format_message(
                prefix,
                invalid_hosts_message,
                account_number=account_number,
                report_id=report_id))

    invalid_host_dict_format = False
    for host_id, host in hosts.items():
        if not isinstance(host_id, str) or not isinstance(host, dict):
            invalid_host_dict_format = True
            break

    if invalid_host_dict_format:
        raise QPCReportException(
            format_message(
                prefix,
                invalid_hosts_message,
                account_number=account_number,
                report_id=report_id))

    valid_hosts, invalid_hosts = verify_report_hosts(account_number, insights_report)
    number_valid = len(valid_hosts)
    total = number_valid + len(invalid_hosts)
    LOG.info(format_message(
        prefix,
        '%s/%s hosts are valid.' % (
            number_valid, total),
        account_number=account_number,
        report_id=report_id
    ))
    if not valid_hosts:
        raise QPCReportException(
            format_message(
                prefix,
                'report does not contain any valid hosts.',
                account_number=account_number,
                report_id=report_id))
    else:
        return valid_hosts, invalid_hosts


def verify_report_hosts(account_number, insights_report):
    """Verify that report hosts contain canonical facts.

    :param account_number: the account number associated with report
    :param insights_report: dict with report
    """
    hosts = insights_report['hosts']
    report_id = insights_report['report_platform_id']

    prefix = 'VALIDATE HOSTS'
    valid_hosts = {}
    invalid_hosts = {}
    for host_id, host in hosts.items():
        found_facts = False
        for fact in CANONICAL_FACTS:
            if host.get(fact):
                found_facts = True
                break
        if found_facts:
            valid_hosts[host_id] = host
        else:
            host.pop('metadata', None)
            invalid_hosts[host_id] = host
    if invalid_hosts:
        LOG.warning(
            format_message(
                prefix,
                'Removed %d hosts with 0 canonical facts: %s' % (
                    len(invalid_hosts), invalid_hosts),
                account_number=account_number,
                report_id=report_id))

    # Invalid hosts is for future use.
    return valid_hosts, invalid_hosts


async def send_confirmation(file_hash, status, account_number=None, report_id=None):  # pragma: no cover
    """
    Send kafka validation message to Insights Upload service.

    When a new file lands for topic 'qpc' we must validate it
    so that it will be made permanently available to other
    apps listening on the 'available' topic.
    :param: file_hash (String): Hash for file being confirmed.
    :param: status (String): Either 'success' or 'failure'
    :param account_number: <str> of the User's account number.
    :param report_id: <str> of the report platform id
    :returns None
    """
    prefix = 'REPORT VALIDATION STATE ON KAFKA'
    producer = AIOKafkaProducer(
        loop=PROCESSING_LOOP, bootstrap_servers=INSIGHTS_KAFKA_ADDRESS
    )
    try:
        await producer.start()
    except (KafkaConnectionError, TimeoutError):
        await producer.stop()
        raise KafkaMsgHandlerError(
            format_message(
                prefix,
                'Unable to connect to kafka server.  Closing producer.',
                account_number=account_number,
                report_id=report_id))
    try:
        validation = {
            'hash': file_hash,
            'validation': status
        }
        msg = bytes(json.dumps(validation), 'utf-8')
        await producer.send_and_wait(VALIDATION_TOPIC, msg)
        LOG.info(
            format_message(
                prefix,
                'Send %s validation status to file upload on kafka' % status,
                account_number=account_number,
                report_id=report_id))
    finally:
        await producer.stop()


def upload_to_host_inventory(account_number, report_id, hosts):
    """
    Verify that the report contents are a valid Insights report.

    :param account_number: <str> of the User's account number.
    :param report_id: <str> of the report platform id
    :param hosts: a list of dictionaries that have been validated.
    :returns None
    """
    prefix = 'UPLOAD TO HOST INVENTORY'
    identity_string = '{"identity": {"account_number": "%s"}}' % str(account_number)
    bytes_string = identity_string.encode()
    x_rh_identity_value = base64.b64encode(bytes_string).decode()
    identity_header = {'x-rh-identity': x_rh_identity_value,
                       'Content-Type': 'application/json'}
    failed_hosts = []
    successful_hosts = {}  # storing the hosts that successfully upload here
    failed_upload_hosts = {}  # storing the hosts that failed to upload here
    for host_id, host in hosts.items():
        body = {}
        body['account'] = account_number
        body['bios_uuid'] = host.get('bios_uuid')
        body['display_name'] = host.get('name')
        body['ip_addresses'] = host.get('ip_addresses')
        body['mac_addresses'] = host.get('mac_addresses')
        body['insights_id'] = host.get('insights_client_id')
        body['rhel_machine_id'] = host.get('etc_machine_id')
        body['subscription_manager_id'] = host.get('subscription_manager_id')
        body['fqdn'] = host.get('name')
        body['facts'] = [{
            'namespace': 'qpc',
            'facts': host
        }]
        try:
            response = requests.post(INSIGHTS_HOST_INVENTORY_URL,
                                     data=json.dumps(body),
                                     headers=identity_header)

            if response.status_code not in [HTTPStatus.OK, HTTPStatus.CREATED]:
                try:
                    json_body = response.json()
                except ValueError:
                    json_body = 'No JSON response'

                failed_hosts.append(
                    {
                        'status_code': response.status_code,
                        'error': json_body,
                        'display_name': body.get('display_name'),
                        'system_platform_id': host_id,
                        'host': host})
                failed_upload_hosts[host_id] = host
            else:
                successful_hosts[host_id] = host

        except requests.exceptions.RequestException as err:
            failed_hosts.append(
                {
                    'status_code': 'None',
                    'error': str(err),
                    'display_name': body.get('display_name'),
                    'system_platform_id': host_id,
                    'host': host})
            failed_upload_hosts[host_id] = host

    successful = len(hosts) - len(failed_hosts)
    upload_msg = format_message(
        prefix, '%s/%s hosts uploaded to host inventory' %
        (successful, len(hosts)),
        account_number=account_number,
        report_id=report_id
    )
    if successful != len(hosts):
        LOG.warning(upload_msg)
    else:
        LOG.info(upload_msg)
    if failed_hosts:
        for failed_info in failed_hosts:
            LOG.error(format_message(
                prefix,
                'Host inventory returned %s for %s.  Error: %s.  '
                'system_platform_id: %s. host: %s' % (
                    failed_info.get('status_code'),
                    failed_info.get('display_name'),
                    failed_info.get('error'),
                    failed_info.get('system_platform_id'),
                    failed_info.get('host')),
                account_number=account_number,
                report_id=report_id
            ))
    return successful_hosts, failed_upload_hosts


async def save_message_and_ack(consumer):
    """Save and ack the kafka uploaded message."""
    prefix = 'SAVING MESSAGE'
    while True:
        consumer_record = await MSG_PENDING_QUEUE.get()
        if consumer_record.topic == QPC_TOPIC:
            try:
                upload_service_message = unpack_consumer_record(consumer_record)
                rh_account = upload_service_message.get('rh_account')
                if not rh_account:
                    raise QPCKafkaMsgException(
                        format_message(
                            prefix,
                            'Message missing rh_account.'))
                try:
                    uploaded_report = Report(upload_srv_kafka_msg=json.dumps(upload_service_message),
                                             rh_account=rh_account,
                                             state=Report.NEW,
                                             state_info=json.dumps([Report.NEW]),
                                             last_update_time=datetime.utcnow(),
                                             candidate_hosts=json.dumps({}),
                                             failed_hosts=json.dumps([]),
                                             retry_count=0)
                    uploaded_report.save()
                    LOG.info(format_message(prefix, 'Upload service message saved. Ready for processing.'))
                    consumer.commit()
                except Exception as error:
                    LOG.error(format_message(prefix, 'Could not save upload service message: %s', error))
            except QPCKafkaMsgException as message_error:
                LOG.error(format_message(prefix, 'Error processing records.  Message: %s, Error: %s',
                                         consumer_record, message_error))
                consumer.commit()


class MessageProcessor():
    """Class for processing saved upload service messages."""

    def __init__(self):
        """Create a message processor."""
        self.report = None
        self.state = None
        self.new_state = None
        self.account_number = None
        self.upload_message = None
        self.report_id = None
        self.report_json = None
        self.candidate_hosts = None
        self.failed_hosts = None
        self.status = None
        self.prefix = 'PROCESSING REPORT'

    def assign_report(self):
        """Assign the message processor reports that are saved in the db.

        First priority is the oldest reports that are in the new state.
        If no reports meet this condition, we look for the oldest report in any state.
        We then check to see if an appropriate amount of time has passed before we retry this
        report.
        """
        if self.report is None:
            try:
                # look for the oldest report in the new state
                self.report = Report.objects.filter(state=Report.NEW).earliest('last_update_time')
            except Report.DoesNotExist:
                try:
                    # if there are no new reports, look for the oldest report in the db
                    oldest_report = Report.objects.earliest('last_update_time')
                    current_time = datetime.utcnow()
                    # check that the last updated time on the report is older than the retry time
                    minutes_passed = int((current_time - oldest_report.last_update_time).total_seconds() / 60)
                    if minutes_passed >= RETRY_TIME:
                        self.report = oldest_report
                except Report.DoesNotExist:
                    pass

    def update_report_state(self, retry=None, report_json=None, report_id=None,
                            candidate_hosts=None, failed_hosts=None):
        """
        Update the message processor state and save.

        :param retry: <bool> boolean on whether or not we should increase the retry
        :param report_json: <dict> dictionary containing the report json
        :param report_id: <str> string containing report_platform_id
        :param candidate_hosts: <dict> dictionary containing hosts that were
            successfully verified and uploaded
        :param failed_hosts: <dict> dictionary containing hosts that failed
            verification or upload
        """
        try:
            self.report.last_update_time = datetime.utcnow()
            self.report.state = self.new_state
            if retry is not None:
                if retry:
                    self.report.retry_count += 1
            else:
                self.report.retry_count = 0
            if report_json:
                self.report.report_json = json.dumps(report_json)
            if report_id:
                self.report.report_platform_id = report_id
            if candidate_hosts:
                # for success hosts, these can change based on the function
                # ie. hosts may pass verification but not upload so we
                # completely override the previous value to get only the
                # successful hosts at that point. Also, we must remove the
                # hosts that succeeded from the failed hosts in case they were
                # put there on a retry.
                self.remove_success_from_failure(candidate_hosts)
                self.report.candidate_hosts = json.dumps(candidate_hosts)
            if failed_hosts:
                # for failed hosts this list can keep growing, so we add the
                # newly failed hosts to the previous value
                failed = json.loads(self.report.failed_hosts)
                for host in failed_hosts:
                    failed.append(host)
                self.report.failed_hosts = json.dumps(failed)
            state_info = json.loads(self.report.state_info)
            state_info.append(self.new_state)
            self.report.state_info = json.dumps(state_info)
            self.report.save()
        except Exception as error:
            LOG.error(
                self.prefix,
                'Could not update report record due to the following error %s.', error)

    def determine_retry(self, fail_state, current_state, failed_hosts=None):
        """Determine if yupana should archive a report based on retry count.

        :param fail_state: <str> the final state if we have reached max retries
        :param current_state: <str> the current state we are in that we want to try again
        :param failed_hosts: <list> for uploading to host inventory, if we fail due to
            no hosts being successfully uploaded, we want to rerecord the hosts that failed
            for retrying.
        """
        if self.report.retry_count + 1 > RETRIES_ALLOWED:
            self.status = FAILURE_CONFIRM_STATUS
            self.new_state = fail_state
            self.update_report_state(False)
        else:
            self.new_state = current_state
            if failed_hosts:
                self.update_report_state(True, None, None, self.candidate_hosts, failed_hosts)
            else:
                self.update_report_state(True)
            self.reinit_variables()

    def assign_cause_to_failed(self, cause):
        """Assign the reason for failure to the failed_hosts.

        :param cause: <str> should be 'VERIFICATION" or 'UPLOAD'
        """
        failed_hosts_list = []
        for host_id, host in self.failed_hosts.items():
            failed_hosts_list.append({host_id: host, 'cause': cause})
        return failed_hosts_list

    def generate_retry_candidates(self):
        """Generate hosts that only failed upload to retry."""
        failed_hosts_list = json.loads(self.report.failed_hosts)
        success_hosts = json.loads(self.report.candidate_hosts)
        retry_hosts = {}
        for host in failed_hosts_list:
            if host.get('cause', '') == 'UPLOAD':
                host.pop('cause')
                for key in host.keys():
                    retry_hosts[key] = host[key]
        if retry_hosts:
            return retry_hosts
        return success_hosts

    def remove_success_from_failure(self, success_hosts):
        """Remove the hosts that succeeded from the failed hosts if they are there."""
        failed_hosts = json.loads(self.report.failed_hosts)
        for host_id, host in success_hosts.items():
            for failed_host in failed_hosts:
                if failed_host.get(host_id):
                    failed_hosts.remove(failed_host)
        self.report.failed_hosts = json.dumps(failed_hosts)
        self.report.save()

    def deduplicate_reports(self):
        """If a report with the same id already exists, archive the new report."""
        try:
            existing_reports = Report.objects.filter(report_platform_id=self.report.report_platform_id)
            if existing_reports.count() > 1:
                LOG.error(format_message(
                    self.prefix,
                    'a report with this report_platform_id already exists. Archiving report'))
                self.archive_report()
        except Report.DoesNotExist:
            pass

    def start(self):
        """Change the state from NEW to STARTED."""
        LOG.info(format_message(self.prefix, 'Starting report processor'))
        self.new_state = Report.STARTED
        self.update_report_state()

    def download(self):
        """Attempt to download the report and extract the json."""
        LOG.info(format_message(self.prefix, 'Attempting to download the report and extract the json'))
        try:
            report_tar_gz = download_report(self.account_number, self.upload_message)
            self.report_json = extract_report_from_tar_gz(self.account_number, report_tar_gz)
            self.new_state = Report.DOWNLOADED
            self.update_report_state(None, self.report_json)
        except (QPCReportException, QPCKafkaMsgException):
            self.determine_retry(Report.FAILED_DOWNLOAD, Report.STARTED)

    def verify(self):
        """Verify that the downloaded report is a valid insights report."""
        LOG.info(format_message(self.prefix, 'Validating the report contents'))
        try:
            self.candidate_hosts, self.failed_hosts = verify_report_details(
                self.account_number, self.report_json)
            failed_hosts_list = self.assign_cause_to_failed('VERIFICATION')
            self.report_id = self.report_json.get('report_platform_id')
            self.status = SUCCESS_CONFIRM_STATUS
            self.new_state = Report.VALIDATED
            if not self.candidate_hosts:
                self.status = FAILURE_CONFIRM_STATUS
            self.update_report_state(None, None, self.report_id, self.candidate_hosts, failed_hosts_list)
            self.deduplicate_reports()
        except Exception as error:
            LOG.error(error)
            self.determine_retry(Report.FAILED_VALIDATION, Report.DOWNLOADED)

    async def send_validation(self):
        """Upload the validation status of the message."""
        LOG.info(format_message(self.prefix, 'Uploading validation status %s for report %s',
                                self.status, self.report_id))
        message_hash = self.upload_message['hash']
        try:
            await send_confirmation(message_hash,
                                    self.status,
                                    account_number=self.account_number,
                                    report_id=self.report_id)
            self.new_state = Report.VALIDATION_REPORTED
            self.update_report_state()
            # update_report_state(self.report, Report.VALIDATION_REPORTED)
        except Exception as error:
            LOG.error(error)
            self.determine_retry(Report.FAILED_VALIDATION_REPORTING, Report.VALIDATED)

    def upload(self):
        """Upload the host candidates to the host_inventory."""
        LOG.info(format_message(self.prefix, 'Uploading hosts to inventory'))
        try:
            hosts_to_try = self.generate_retry_candidates()
            self.candidate_hosts, self.failed_hosts = upload_to_host_inventory(
                self.account_number,
                self.report_id,
                hosts_to_try)
            self.new_state = Report.HOSTS_UPLOADED
            failed_hosts_list = self.assign_cause_to_failed('UPLOAD')
            if self.candidate_hosts:
                self.update_report_state(None, None, None, self.candidate_hosts, failed_hosts_list)
            else:
                # self.new_state = Report.VALIDATION_REPORTED
                self.determine_retry(Report.FAILED_HOSTS_UPLOAD, Report.VALIDATION_REPORTED)
                # self.update_report_state(True, None, None, self.candidate_hosts, failed_hosts_list)
        except Exception as error:
            LOG.error(error)
            self.determine_retry(Report.FAILED_HOSTS_UPLOAD, Report.VALIDATION_REPORTED)

    def reinit_variables(self):
        """Reinit the class variables to None."""
        self.report = None
        self.state = None
        self.account_number = None
        self.upload_message = None
        self.report_id = None
        self.report_json = None
        self.candidate_hosts = None
        self.failed_hosts = None
        self.status = None

    def archive_report(self):
        """Archive the report object."""
        LOG.info(format_message(self.prefix, 'Archiving report'))
        archived = ReportArchive(
            rh_account=self.account_number,
            retry_count=self.report.retry_count,
            candidate_hosts=self.report.candidate_hosts,
            failed_hosts=self.report.failed_hosts,
            state=self.report.state,
            state_info=self.report.state_info,
            last_update_time=self.report.last_update_time,
            upload_srv_kafka_msg=self.upload_message
        )
        if self.report_id:
            archived.report_platform_id = self.report_id
        if self.report_json:
            archived.report_json = self.report_json
        archived.save()
        try:
            Report.objects.get(id=self.report.id).delete()
        except Report.DoesNotExist:
            pass
        self.reinit_variables()

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
        async_function_call_states = [Report.VALIDATED]
        state_functions = {Report.NEW: self.start,
                           Report.STARTED: self.download,
                           Report.DOWNLOADED: self.verify,
                           Report.VALIDATED: self.send_validation,
                           Report.VALIDATION_REPORTED: self.upload,
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

    async def run(self):
        """Run the message processor for a report."""
        while True:
            self.assign_report()
            if self.report:
                await self.delegate_state()


async def listen_for_messages(consumer):  # pragma: no cover
    """
    Listen for messages on the available and qpc topics.

    Once a message from one of these topics arrives, we add
    them to the MSG_PENDING_QUEUE.
    :param consumer : Kafka consumer
    :returns None
    """
    try:
        await consumer.start()
    except KafkaConnectionError:
        await consumer.stop()
        raise KafkaMsgHandlerError('Unable to connect to kafka server.  Closing consumer.')

    LOG.info('Listener started.  Waiting for messages...')
    try:
        # Consume messages
        async for msg in consumer:
            await MSG_PENDING_QUEUE.put(msg)
    finally:
        # Will leave consumer group; perform autocommit if enabled.
        await consumer.stop()


def asyncio_worker_thread(loop):  # pragma: no cover
    """
    Worker thread function to run the asyncio event loop.

    :param None
    :returns None
    """
    consumer = AIOKafkaConsumer(
        AVAILABLE_TOPIC, QPC_TOPIC,
        loop=EVENT_LOOP, bootstrap_servers=INSIGHTS_KAFKA_ADDRESS,
        group_id='qpc-group', session_timeout_ms=SESSION_TIMEOUT,
        request_timeout_ms=REQUEST_TIMEOUT,
        enable_auto_commit=True
    )

    loop.create_task(save_message_and_ack(consumer))

    try:
        loop.run_until_complete(listen_for_messages(consumer))
    except KafkaMsgHandlerError as err:
        LOG.info('Stopping kafka worker thread.  Error: %s', str(err))


def initialize_kafka_handler():  # pragma: no cover
    """
    Create asyncio tasks and daemon thread to run event loop.

    :param None
    :returns None
    """
    event_loop_thread = threading.Thread(target=asyncio_worker_thread, args=(EVENT_LOOP,))
    event_loop_thread.daemon = True
    event_loop_thread.start()


def asyncio_message_processor_thread(loop):  # pragma: no cover
    """
    Worker thread function to run the asyncio event loop.

    :param None
    :returns None
    """
    processor = MessageProcessor()
    loop.run_until_complete(processor.run())


def initialize_message_processor():  # pragma: no cover
    """
    Create asyncio tasks and daemon thread to run event loop.

    :param None
    :returns None
    """
    event_loop_thread = threading.Thread(target=asyncio_message_processor_thread, args=(PROCESSING_LOOP,))
    event_loop_thread.daemon = True
    event_loop_thread.start()
