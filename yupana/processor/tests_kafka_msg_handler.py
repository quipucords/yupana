#
# Copyright 2018 Red Hat, Inc.
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
"""Tests kafka message handler."""

import io
import json
import tarfile
import uuid
from unittest.mock import patch

import processor.kafka_msg_handler as msg_handler
import requests
import requests_mock
from django.test import TestCase
from requests.exceptions import HTTPError


def create_tar_buffer(files_data):
    """Generate a file buffer based off a dictionary."""
    if not isinstance(files_data, (dict,)):
        return None
    if not all(isinstance(v, (str, dict)) for v in files_data.values()):
        return None
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode='w:gz') as tar_file:
        for file_name, file_content in files_data.items():
            if file_name.endswith('json'):
                file_buffer = \
                    io.BytesIO(json.dumps(file_content).encode('utf-8'))
            elif file_name.endswith('csv'):
                file_buffer = io.BytesIO(file_content.encode('utf-8'))
            else:
                return None
            info = tarfile.TarInfo(name=file_name)
            info.size = len(file_buffer.getvalue())
            tar_file.addfile(tarinfo=info, fileobj=file_buffer)
    tar_buffer.seek(0)
    return tar_buffer.getvalue()


class KafkaMsg:
    """Create a kafka msg."""

    def __init__(self, topic, url):
        """Initialize the message."""
        self.topic = topic
        value_dict = {'url': url, 'rh_account': '1234'}
        value_str = json.dumps(value_dict)
        self.value = value_str.encode('utf-8')


class KafkaMsgHandlerTest(TestCase):
    """Test Cases for the Kafka msg handler."""

    def setUp(self):
        """Create test setup."""
        self.payload_url = 'http://insights-upload.com/q/file_to_validate'
        self.uuid = str(uuid.uuid4())
        self.uuid2 = str(uuid.uuid4())
        self.uuid3 = str(uuid.uuid4())
        self.uuid4 = str(uuid.uuid4())
        self.uuid5 = str(uuid.uuid4())
        self.uuid6 = str(uuid.uuid4())
        self.uuid7 = str(uuid.uuid4())

    def tearDown(self):
        """Remove test setup."""
        pass

    def test_format_message_no_account_report(self):
        """Test format message without account or report id."""
        msg = msg_handler.format_message('p', 'm')
        self.assertEquals(msg, 'Report p - m')

    def test_unpack_consumer_record(self):
        """Test format message without account or report id."""
        fake_record = KafkaMsg(msg_handler.QPC_TOPIC, 'http://internet.com')
        msg = msg_handler.unpack_consumer_record(fake_record)
        self.assertEquals(msg, {'url': 'http://internet.com', 'rh_account': '1234'})

    def test_unpack_consumer_record_not_json(self):
        """Test format message without account or report id."""
        fake_record = KafkaMsg(msg_handler.QPC_TOPIC, 'http://internet.com')
        fake_record.value = 'not json'.encode('utf-8')

        with self.assertRaises(msg_handler.QPCKafkaMsgException):
            msg_handler.unpack_consumer_record(fake_record)

    def test_verify_report_success(self):
        """Test to verify a QPC report with the correct structure passes validation."""
        report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'bios_uuid': 'value'}}}

        valid, invalid = msg_handler.verify_report_details('1234', report_json)
        expect_valid = {self.uuid: {'bios_uuid': 'value'}}
        expect_invalid = {}
        self.assertEqual(valid, expect_valid)
        self.assertEqual(invalid, expect_invalid)

    def test_verify_report_success_mixed_hosts(self):
        """Test to verify a QPC report with the correct structure passes validation."""
        report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'bios_uuid': 'value'},
                      self.uuid2: {'invalid': 'value'}}}

        valid, invalid = msg_handler.verify_report_details('12345', report_json)
        expect_valid = {self.uuid: {'bios_uuid': 'value'}}
        expect_invalid = {self.uuid2: {'invalid': 'value'}}
        self.assertEqual(valid, expect_valid)
        self.assertEqual(invalid, expect_invalid)

    def test_verify_report_missing_id(self):
        """Test to verify a QPC report with a missing id is failed."""
        report_json = {
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'key': 'value'}}}

        with self.assertRaises(msg_handler.QPCReportException):
            _, _ = msg_handler.verify_report_details('1234', report_json)

    def test_verify_report_fails_no_canonical_facts(self):
        """Test to verify a QPC report with the correct structure passes validation."""
        report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'name': 'value'}}}

        with self.assertRaises(msg_handler.QPCReportException):
            _, _ = msg_handler.verify_report_details('1234', report_json)

    def test_verify_report_invalid_report_type(self):
        """Test to verify a QPC report with an invalid report_type is failed."""
        report_json = {
            'report_id': 1,
            'report_type': 'details',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'key': 'value'}}}

        with self.assertRaises(msg_handler.QPCReportException):
            _, _ = msg_handler.verify_report_details('1234', report_json)

    def test_verify_report_missing_version(self):
        """Test to verify a QPC report missing report_version is failed."""
        report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'key': 'value'}}}

        with self.assertRaises(msg_handler.QPCReportException):
            _, _ = msg_handler.verify_report_details('1234', report_json)

    def test_verify_report_missing_platform_id(self):
        """Test to verify a QPC report missing report_platform_id is failed."""
        report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'hosts': {self.uuid: {'key': 'value'}}}

        with self.assertRaises(msg_handler.QPCReportException):
            _, _ = msg_handler.verify_report_details('1234', report_json)

    def test_verify_report_missing_hosts(self):
        """Test to verify a QPC report with empty hosts is failed."""
        report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {}}

        with self.assertRaises(msg_handler.QPCReportException):
            _, _ = msg_handler.verify_report_details('1234', report_json)

    def test_verify_report_hosts(self):
        """Test host verification."""
        # test all valid hosts
        uuid8 = str(uuid.uuid4())
        uuid9 = str(uuid.uuid4())

        valid = {self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo'},
                 self.uuid3: {'ip_addresses': 'value', 'name': 'foo'},
                 self.uuid4: {'mac_addresses': 'value', 'name': 'foo'},
                 self.uuid5: {'vm_uuid': 'value', 'name': 'foo'},
                 self.uuid6: {'etc_machine_id': 'value'},
                 self.uuid7: {'subscription_manager_id': 'value'}
                 }
        invalid = {uuid8: {'not_valid': 'value'}}
        hosts = dict(valid)
        hosts.update(invalid)
        report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': hosts}
        actual_valid, actual_invalid = msg_handler.verify_report_hosts('1234',
                                                                       report_json)
        self.assertEqual(actual_valid, valid)
        self.assertEqual(actual_invalid, invalid)

        # test that invalid hosts are removed
        invalid_host = {uuid9: {'no': 'canonical facts', 'metadata': []}}
        hosts.update(invalid_host)
        valid_hosts, _ = msg_handler.verify_report_hosts('1234',
                                                         report_json)
        self.assertEqual(valid_hosts.get(uuid9), None)

        # test that if there are no valid hosts we return {}
        report_json['hosts'] = invalid_host
        valid_hosts, _ = msg_handler.verify_report_hosts('1234',
                                                         report_json)
        self.assertEqual({}, valid_hosts)

    def test_extract_report_from_tar_gz_success(self):
        """Testing the extract method with valid buffer content."""
        report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'key': 'value'}}}
        test_dict = dict()
        test_dict['file.json'] = report_json
        buffer_content = create_tar_buffer(test_dict)
        result = msg_handler.extract_report_from_tar_gz('1234', buffer_content)
        self.assertEqual(result, report_json)

    def test_extract_report_from_tar_gz_failure(self):
        """Testing the extract method failure too many json files."""
        report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'key': 'value'}}}
        test_dict = dict()
        test_dict['file.json'] = report_json
        test_dict['file_2.json'] = report_json
        buffer_content = create_tar_buffer(test_dict)
        with self.assertRaises(msg_handler.QPCReportException):
            msg_handler.extract_report_from_tar_gz('1234', buffer_content)

    def test_extract_report_from_tar_gz_failure_no_json(self):
        """Testing the extract method failure no json file."""
        report_json = 'No valid report'
        test_dict = dict()
        test_dict['file.txt'] = report_json
        buffer_content = create_tar_buffer(test_dict)
        with self.assertRaises(msg_handler.QPCReportException):
            msg_handler.extract_report_from_tar_gz('1234', buffer_content)

    def test_extract_report_from_tar_gz_failure_invalid_json(self):
        """Testing the extract method failure invalid json."""
        report_json = None
        test_dict = dict()
        test_dict['file.json'] = report_json
        buffer_content = create_tar_buffer(test_dict)
        with self.assertRaises(msg_handler.QPCReportException):
            msg_handler.extract_report_from_tar_gz('1234', buffer_content)

    def test_download_response_content_bad_url(self):
        """Test to verify extracting payload exceptions are handled."""
        with requests_mock.mock() as m:
            m.get(self.payload_url, exc=HTTPError)
            with self.assertRaises(msg_handler.QPCReportException):
                msg_handler.download_report('1234', {'url': self.payload_url})

    def test_download_response_content_missing_url(self):
        """Test case where url is missing."""
        with requests_mock.mock() as m:
            m.get(self.payload_url, exc=HTTPError)
            with self.assertRaises(msg_handler.QPCReportException):
                msg_handler.download_report('1234', {})

    def test_download_report_success(self):
        """Test to verify extracting contents is successful."""
        report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'insights_client_id': 'value'}}}
        value = {'url': self.payload_url, 'rh_account': '00001'}
        test_dict = dict()
        test_dict['file.json'] = report_json
        buffer_content = create_tar_buffer(test_dict)
        with requests_mock.mock() as m:
            m.get(self.payload_url, content=buffer_content)
            content = msg_handler.download_report('1234', value)
            self.assertEqual(buffer_content, content)

    def test_download_and_validate_contents_invalid_report(self):
        """Test to verify extracting contents fails when report is invalid."""
        report_json = {
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'key': 'value'}}}

        with self.assertRaises(msg_handler.QPCReportException):
            _, _ = msg_handler.verify_report_details('1234', report_json)

    def test_download_and_validate_contents_raises_error(self):
        """Test to verify extracting contents fails when error is raised."""
        report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'key': 'value'}}}
        value = {'url': self.payload_url, 'rh_account': '00001'}
        test_dict = dict()
        test_dict['file.json'] = report_json
        buffer_content = create_tar_buffer(test_dict)
        with requests_mock.mock() as m:
            m.get(self.payload_url, content=buffer_content)
            with patch('requests.get', side_effect=HTTPError):
                with self.assertRaises(msg_handler.QPCReportException):
                    content = msg_handler.download_report('1234', value)
                    self.assertEqual(content, buffer_content)

    def test_download_with_404(self):
        """Test downloading a URL and getting 404."""
        report_json = {}
        test_dict = dict()
        test_dict['file.json'] = report_json
        with requests_mock.mock() as m:
            m.get(self.payload_url, status_code=404)
            with self.assertRaises(msg_handler.QPCKafkaMsgException):
                msg_handler.download_report('1234', {'url': self.payload_url})

    def test_value_error_extract_report_from_tar_gz(self):
        """Testing value error when extracting json from tar.gz."""
        invalid_json = '["report_id": 1]'
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode='w:gz') as tar_file:
            file_name = 'file.json'
            file_content = invalid_json
            file_buffer = io.BytesIO(file_content.encode('utf-8'))
            info = tarfile.TarInfo(name=file_name)
            info.size = len(file_buffer.getvalue())
            tar_file.addfile(tarinfo=info, fileobj=file_buffer)
        tar_buffer.seek(0)
        buffer_content = tar_buffer.getvalue()
        with self.assertRaises(msg_handler.QPCReportException):
            msg_handler.extract_report_from_tar_gz('1234', buffer_content)

    def test_no_json_files_extract_report_from_tar_gz(self):
        """Testing no json files found in tar.gz."""
        invalid_json = '["report_id": 1]'
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode='w:gz') as tar_file:
            file_name = 'file.csv'
            file_content = invalid_json
            file_buffer = io.BytesIO(file_content.encode('utf-8'))
            info = tarfile.TarInfo(name=file_name)
            info.size = len(file_buffer.getvalue())
            tar_file.addfile(tarinfo=info, fileobj=file_buffer)
        tar_buffer.seek(0)
        buffer_content = tar_buffer.getvalue()
        with self.assertRaises(msg_handler.QPCReportException):
            msg_handler.extract_report_from_tar_gz('1234', buffer_content)

    def test_no_account_number_inventory_upload(self):
        """Testing no account number present when uploading to inventory."""
        account_number = None
        report_platform_id = '0001-kevan'
        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo'},
                 self.uuid3: {'ip_addresses': 'value', 'name': 'foo'},
                 self.uuid4: {'mac_addresses': 'value', 'name': 'foo'},
                 self.uuid5: {'vm_uuid': 'value', 'name': 'foo'},
                 self.uuid6: {'etc_machine_id': 'value'},
                 self.uuid7: {'subscription_manager_id': 'value'}}
        msg_handler.upload_to_host_inventory(account_number,
                                             report_platform_id,
                                             hosts)

    def test_successful_host_inventory_upload(self):
        """Testing successful upload to host inventory."""
        account_number = '00001'
        report_platform_id = '0001-kevan'
        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo'},
                 self.uuid3: {'ip_addresses': 'value', 'name': 'foo'},
                 self.uuid4: {'mac_addresses': 'value', 'name': 'foo'},
                 self.uuid5: {'vm_uuid': 'value', 'name': 'foo'},
                 self.uuid6: {'etc_machine_id': 'value'},
                 self.uuid7: {'subscription_manager_id': 'value'}}
        with patch('processor.kafka_msg_handler.INSIGHTS_HOST_INVENTORY_URL', value='not none'):
            with patch('processor.kafka_msg_handler.requests', status_code=200):
                msg_handler.upload_to_host_inventory(account_number,
                                                     report_platform_id,
                                                     hosts)

    @patch('processor.kafka_msg_handler.requests.post')
    def test_host_url_exceptions(self, mock_request):
        """Testing an exception being raised during host inventory upload."""
        good_resp = requests.Response()
        good_resp.status_code = 200
        bad_resp = requests.exceptions.ConnectionError()
        mock_request.side_effect = [good_resp, bad_resp]
        account_number = '00001'
        report_platform_id = '0001-kevan'
        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo'}}
        with patch('processor.kafka_msg_handler.INSIGHTS_HOST_INVENTORY_URL', value='not none'):
            msg_handler.upload_to_host_inventory(account_number,
                                                 report_platform_id,
                                                 hosts)

    def test_verify_report_fails_not_dict(self):
        """Test to verify a QPC report with not dict."""
        report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': ['foo']}
        value = {'url': self.payload_url, 'rh_account': '00001'}
        test_dict = dict()
        test_dict['file.json'] = report_json
        buffer_content = create_tar_buffer(test_dict)
        with requests_mock.mock() as m:
            m.get(self.payload_url, content=buffer_content)
            with patch('requests.get', side_effect=HTTPError):
                with self.assertRaises(msg_handler.QPCReportException):
                    content = msg_handler.download_report('1234', value)
                    self.assertEqual(content, buffer_content)

    def test_verify_report_fails_invalid_key(self):
        """Test to verify a QPC report with dict not str key."""
        report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {1: {'name': 'value'}}}
        value = {'url': self.payload_url, 'rh_account': '00001'}
        test_dict = dict()
        test_dict['file.json'] = report_json
        buffer_content = create_tar_buffer(test_dict)
        with requests_mock.mock() as m:
            m.get(self.payload_url, content=buffer_content)
            with patch('requests.get', side_effect=HTTPError):
                with self.assertRaises(msg_handler.QPCReportException):
                    content = msg_handler.download_report('1234', value)
                    self.assertEqual(content, buffer_content)

    def test_verify_report_fails_invalid_value(self):
        """Test to verify a QPC report with an array for value in hosts."""
        report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: ['name']}}
        value = {'url': self.payload_url, 'rh_account': '00001'}
        test_dict = dict()
        test_dict['file.json'] = report_json
        buffer_content = create_tar_buffer(test_dict)
        with requests_mock.mock() as m:
            m.get(self.payload_url, content=buffer_content)
            with patch('requests.get', side_effect=HTTPError):
                with self.assertRaises(msg_handler.QPCReportException):
                    content = msg_handler.download_report('1234', value)
                    self.assertEqual(content, buffer_content)
