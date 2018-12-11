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
        value_dict = {'url': url}
        value_str = json.dumps(value_dict)
        self.value = value_str.encode('utf-8')


class KafkaMsgHandlerTest(TestCase):
    """Test Cases for the Kafka msg handler."""

    def setUp(self):
        """Create test setup."""
        self.payload_url = 'http://insights-upload.com/quarnantine/file_to_validate'

    def tearDown(self):
        """Remove test setup."""
        pass

    def test_verify_report_success(self):
        """Test to verify a QPC report with the correct structure passes validation."""
        report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'system_fingerprints': [{'bios_uuid': 'value'}]}

        status = msg_handler.verify_report_details(report_json)
        accept_return = (msg_handler.SUCCESS_CONFIRM_STATUS,
                         [{'bios_uuid': 'value'}],
                         None)
        self.assertEqual(status, accept_return)

    def test_verify_report_success_mixed_fingerprints(self):
        """Test to verify a QPC report with the correct structure passes validation."""
        report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'system_fingerprints': [{'bios_uuid': 'value'}, {'invalid': 'value'}]}

        status = msg_handler.verify_report_details(report_json)
        accept_return = (msg_handler.SUCCESS_CONFIRM_STATUS,
                         [{'bios_uuid': 'value'}],
                         [{'invalid': 'value'}])
        self.assertEqual(status, accept_return)

    def test_verify_report_missing_id(self):
        """Test to verify a QPC report with a missing id is failed."""
        report_json = {
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'system_fingerprints': [{'key': 'value'}]}

        status = msg_handler.verify_report_details(report_json)
        failure = (msg_handler.FAILURE_CONFIRM_STATUS, None, None)
        self.assertEqual(status, failure)

    def test_verify_report_fails_no_canonical_facts(self):
        """Test to verify a QPC report with the correct structure passes validation."""
        report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'system_fingerprints': [{'name': 'value'}]}

        status = msg_handler.verify_report_details(report_json)
        failure = (msg_handler.FAILURE_CONFIRM_STATUS, None, None)
        self.assertEqual(status, failure)

    def test_verify_report_invalid_report_type(self):
        """Test to verify a QPC report with an invalid report_type is failed."""
        report_json = {
            'report_id': 1,
            'report_type': 'details',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'system_fingerprints': [{'key': 'value'}]}

        status = msg_handler.verify_report_details(report_json)
        failure = (msg_handler.FAILURE_CONFIRM_STATUS, None, None)
        self.assertEqual(status, failure)

    def test_verify_report_missing_version(self):
        """Test to verify a QPC report missing report_version is failed."""
        report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'system_fingerprints': [{'key': 'value'}]}

        status = msg_handler.verify_report_details(report_json)
        failure = (msg_handler.FAILURE_CONFIRM_STATUS, None, None)
        self.assertEqual(status, failure)

    def test_verify_report_missing_platform_id(self):
        """Test to verify a QPC report missing report_platform_id is failed."""
        report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'system_fingerprints': [{'key': 'value'}]}

        status = msg_handler.verify_report_details(report_json)
        failure = (msg_handler.FAILURE_CONFIRM_STATUS, None, None)
        self.assertEqual(status, failure)

    def test_verify_report_missing_fingerprints(self):
        """Test to verify a QPC report with empty fingerprints is failed."""
        report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'system_fingerprints': []}

        status = msg_handler.verify_report_details(report_json)
        failure = (msg_handler.FAILURE_CONFIRM_STATUS, None, None)
        self.assertEqual(status, failure)

    def test_verify_report_fingerprints(self):
        """Test fingerprint verification."""
        # test all valid fingerprints
        valid = [{'bios_uuid': 'value', 'name': 'value'},
                 {'insights_client_id': 'value', 'name': 'foo'},
                 {'ip_addresses': 'value', 'name': 'foo'},
                 {'mac_addresses': 'value', 'name': 'foo'},
                 {'vm_uuid': 'value', 'name': 'foo'},
                 {'etc_machine_id': 'value'},
                 {'subscription_manager_id': 'value'}]
        invalid = [{'not_valid': 'value'}]
        fingerprints = valid + invalid
        report_platform_id = 'be3075ac-84d3-4b62-9f5c-a418a36f802d'
        prints = msg_handler.verify_report_fingerprints(fingerprints,
                                                        report_platform_id)
        self.assertEqual(prints[0], valid)
        self.assertEqual(prints[1], invalid)

        # test that invalid fingerprints are removed
        invalid_print = {'no': 'canonical facts', 'metadata': []}
        fingerprints.append(invalid_print)
        valid_prints = msg_handler.verify_report_fingerprints(fingerprints,
                                                              report_platform_id)
        self.assertNotIn(invalid_print, valid_prints)

        # test that if there are no valid fingerprints we return []
        fingerprints = [invalid_print]
        valid_prints = msg_handler.verify_report_fingerprints(fingerprints,
                                                              report_platform_id)
        self.assertEqual([], valid_prints[0])

    def test_extract_tar_gz_success(self):
        """Testing the extract method with valid buffer content."""
        report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'system_fingerprints': [{'key': 'value'}]}
        test_dict = dict()
        test_dict['file.json'] = report_json
        buffer_content = create_tar_buffer(test_dict)
        result = msg_handler.extract_tar_gz(buffer_content)
        self.assertEqual(result, report_json)

    def test_extract_tar_gz_failure(self):
        """Testing the extract method failure too many json files."""
        report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'system_fingerprints': [{'key': 'value'}]}
        test_dict = dict()
        test_dict['file.json'] = report_json
        test_dict['file_2.json'] = report_json
        buffer_content = create_tar_buffer(test_dict)
        result = msg_handler.extract_tar_gz(buffer_content)
        self.assertEqual(False, result)

    def test_extract_tar_gz_failure_no_json(self):
        """Testing the extract method failure no json file."""
        report_json = 'No valid report'
        test_dict = dict()
        test_dict['file.txt'] = report_json
        buffer_content = create_tar_buffer(test_dict)
        result = msg_handler.extract_tar_gz(buffer_content)
        self.assertEqual(False, result)

    def test_extract_tar_gz_failure_invalid_json(self):
        """Testing the extract method failure invalid json."""
        report_json = None
        test_dict = dict()
        test_dict['file.json'] = report_json
        buffer_content = create_tar_buffer(test_dict)
        result = msg_handler.extract_tar_gz(buffer_content)
        self.assertEqual(False, result)

    def test_download_and_validate_contents(self):
        """Test validating the contents."""
        pass

    def test_download_response_contnent_bad_url(self):
        """Test to verify extracting payload exceptions are handled."""
        with requests_mock.mock() as m:
            m.get(self.payload_url, exc=HTTPError)
            with self.assertRaises(msg_handler.KafkaMsgHandlerError):
                msg_handler.download_response_content({'url': self.payload_url})

    def test_download_response_content_success(self):
        """Test to verify extracting contents is successful."""
        report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'system_fingerprints': [{'insights_client_id': 'value'}]}
        value = {'url': self.payload_url, 'rh_account': '00001'}
        test_dict = dict()
        test_dict['file.json'] = report_json
        buffer_content = create_tar_buffer(test_dict)
        with requests_mock.mock() as m:
            m.get(self.payload_url, content=buffer_content)
            status = msg_handler.download_response_content(value)
            self.assertEqual(report_json, status)

    def test_download_and_validate_contents_invalid_report(self):
        """Test to verify extracting contents fails when report is invalid."""
        report_json = {
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'system_fingerprints': [{'key': 'value'}]}
        status = msg_handler.verify_report_details(report_json)
        failure = (msg_handler.FAILURE_CONFIRM_STATUS, None, None)
        self.assertEqual(failure, status)

    def test_download_and_validate_contents_raises_error(self):
        """Test to verify extracting contents fails when error is raised."""
        report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'system_fingerprints': [{'key': 'value'}]}
        value = {'url': self.payload_url, 'rh_account': '00001'}
        test_dict = dict()
        test_dict['file.json'] = report_json
        buffer_content = create_tar_buffer(test_dict)
        with requests_mock.mock() as m:
            m.get(self.payload_url, content=buffer_content)
            with patch('processor.kafka_msg_handler.extract_tar_gz', side_effect=HTTPError):
                with self.assertRaises(msg_handler.KafkaMsgHandlerError):
                    status = msg_handler.download_response_content(value)
                    self.assertEqual(False, status)

    def test_download_and_validate_contents_failure(self):
        """Test to verify extracting contents fails when contents errors."""
        report_json = {}
        test_dict = dict()
        test_dict['file.json'] = report_json
        buffer_content = create_tar_buffer(test_dict)
        with requests_mock.mock() as m:
            m.get(self.payload_url, content=buffer_content)
            with patch('processor.kafka_msg_handler.extract_tar_gz', return_value=False):
                status = msg_handler.download_response_content({'url': self.payload_url})
                self.assertEqual(False, status)

    def test_value_error_extract_tar_gz(self):
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
        status = msg_handler.extract_tar_gz(buffer_content)
        self.assertEqual(False, status)

    def test_no_json_files_extract_tar_gz(self):
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
        status = msg_handler.extract_tar_gz(buffer_content)
        self.assertEqual(False, status)

    def test_no_account_number_inventory_upload(self):
        """Testing no account number present when uploading to inventory."""
        account_number = None
        report_platform_id = '0001-cody'
        fingerprints = [{'bios_uuid': 'value', 'name': 'value'},
                        {'insights_client_id': 'value', 'name': 'foo'},
                        {'ip_addresses': 'value', 'name': 'foo'},
                        {'mac_addresses': 'value', 'name': 'foo'},
                        {'vm_uuid': 'value', 'name': 'foo'},
                        {'etc_machine_id': 'value'},
                        {'subscription_manager_id': 'value'}]
        status = msg_handler.upload_to_host_inventory(account_number,
                                                      fingerprints,
                                                      report_platform_id)
        self.assertEqual(False, status)

    def test_no_host_url_inventory_upload(self):
        """Testing no environment variable for inventory url."""
        account_number = '00001'
        report_platform_id = '0001-cody'
        fingerprints = [{'bios_uuid': 'value', 'name': 'value'},
                        {'insights_client_id': 'value', 'name': 'foo'},
                        {'ip_addresses': 'value', 'name': 'foo'},
                        {'mac_addresses': 'value', 'name': 'foo'},
                        {'vm_uuid': 'value', 'name': 'foo'},
                        {'etc_machine_id': 'value'},
                        {'subscription_manager_id': 'value'}]
        status = msg_handler.upload_to_host_inventory(account_number,
                                                      fingerprints,
                                                      report_platform_id)
        self.assertEqual(False, status)

    def test_successful_host_inventory_upload(self):
        """Testing successful upload to host inventory."""
        account_number = '00001'
        report_platform_id = '0001-cody'
        fingerprints = [{'bios_uuid': 'value', 'name': 'value'},
                        {'insights_client_id': 'value', 'name': 'foo'},
                        {'ip_addresses': 'value', 'name': 'foo'},
                        {'mac_addresses': 'value', 'name': 'foo'},
                        {'vm_uuid': 'value', 'name': 'foo'},
                        {'etc_machine_id': 'value'},
                        {'subscription_manager_id': 'value'}]
        with patch('processor.kafka_msg_handler.INSIGHTS_HOST_INVENTORY_URL', value='not none'):
            with patch('processor.kafka_msg_handler.requests', status_code=200):
                status = msg_handler.upload_to_host_inventory(account_number,
                                                              fingerprints,
                                                              report_platform_id)
        self.assertEqual(True, status)

    @patch('processor.kafka_msg_handler.requests.post')
    def test_host_url_exceptions(self, mock_request):
        """Testing an exception being raised during host inventory upload."""
        good_resp = requests.Response()
        good_resp.status_code = 200
        bad_resp = requests.exceptions.ConnectionError()
        mock_request.side_effect = [good_resp, bad_resp]
        account_number = '00001'
        report_platform_id = '0001-cody'
        fingerprints = [{'bios_uuid': 'value', 'name': 'value'},
                        {'insights_client_id': 'value', 'name': 'foo'}]
        with patch('processor.kafka_msg_handler.INSIGHTS_HOST_INVENTORY_URL', value='not none'):
            status = msg_handler.upload_to_host_inventory(account_number,
                                                          fingerprints,
                                                          report_platform_id)
        self.assertEqual(True, status)
