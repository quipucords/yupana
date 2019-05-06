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
"""Tests the upload message report processor."""

import asyncio
import json
import uuid
from datetime import datetime
from unittest.mock import patch

import pytz
import requests
import requests_mock
from django.test import TestCase
from processor import (abstract_processor,
                       kafka_msg_handler as msg_handler,
                       report_slice_processor,
                       tests_kafka_msg_handler as test_handler)
from prometheus_client import REGISTRY

from api.models import Report, ReportSlice


# pylint: disable=too-many-public-methods
# pylint: disable=protected-access,too-many-lines,too-many-instance-attributes
class ReportProcessorTests(TestCase):
    """Test Cases for the Message processor."""

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
        self.fake_record = test_handler.KafkaMsg(msg_handler.QPC_TOPIC, 'http://internet.com')
        self.msg = msg_handler.unpack_consumer_record(self.fake_record)
        self.report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'bios_uuid': 'value'},
                      self.uuid2: {'invalid': 'value'}}}
        self.report_record = Report(
            upload_srv_kafka_msg=json.dumps(self.msg),
            rh_account='1234',
            state=Report.NEW,
            state_info=json.dumps([Report.NEW]),
            last_update_time=datetime.now(pytz.utc),
            retry_count=0,
            ready_to_archive=False)
        self.report_record.save()

        self.report_slice = ReportSlice(
            report_platform_id=self.uuid,
            report_slice_id=self.uuid2,
            rh_account='13423',
            report_json=json.dumps(self.report_json),
            state=ReportSlice.NEW,
            state_info=[ReportSlice.NEW],
            retry_count=0,
            last_update_time=datetime.now(pytz.utc),
            failed_hosts=[],
            candidate_hosts=[],
            report=self.report_record,
            ready_to_archive=True)
        self.report_slice.save()
        self.report_record.save()
        self.processor = report_slice_processor.ReportSliceProcessor()
        self.processor.report = self.report_slice

    def check_variables_are_reset(self):
        """Check that report processor members have been cleared."""
        processor_attributes = [self.processor.report_id,
                                self.processor.report,
                                self.processor.state,
                                self.processor.account_number,
                                self.processor.upload_message,
                                self.processor.status,
                                self.processor.report_json,
                                self.processor.candidate_hosts,
                                self.processor.failed_hosts]
        for attribute in processor_attributes:
            self.assertEqual(attribute, None)

    async def async_test_delegate_state(self):
        """Set up the test for delegate state."""
        self.report_slice.state = Report.VALIDATED
        self.report_slice.report_platform_id = self.uuid
        self.report_slice.candidate_hosts = \
            json.dumps([{self.uuid3: {'ip_addresses': 'value', 'name': 'value'},
                         'cause': report_slice_processor.FAILED_UPLOAD}])
        self.report_slice.failed_hosts = \
            json.dumps([{self.uuid2: {'ip_addresses': 'value', 'name': 'value'},
                         'cause': abstract_processor.FAILED_VALIDATION}])
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice

        def upload_side_effect():
            """Transition the state to uploaded."""
            self.processor.state = ReportSlice.HOSTS_UPLOADED
            self.report_slice.state = ReportSlice.HOSTS_UPLOADED
            self.report_slice.save()

        with patch(
                'processor.report_slice_processor.'
                'ReportSliceProcessor.transition_to_hosts_uploaded',
                side_effect=upload_side_effect):
            await self.processor.delegate_state()
            self.check_variables_are_reset()
            # self.assertEqual(self.processor.report_id, self.report_slice.report_platform_id)
            # self.assertEqual(self.processor.report_or_slice.state, ReportSlice.HOSTS_UPLOADED)

    def test_run_delegate(self):
        """Test the async function delegate state."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_delegate_state)
        event_loop.run_until_complete(coro())
        event_loop.close()

    def test_transition_to_validated_general_exception(self):
        """Test that when a general exception is raised, we don't pass validation."""
        self.report_slice.state = ReportSlice.RETRY_VALIDATION
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice
        # self.processor.report_json = {
        #     'report_id': 1,
        #     'report_type': 'deployments',
        #     'report_version': '1.0.0.1b025b8',
        #     'status': 'completed',
        #     'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
        #     'hosts': {self.uuid: {'key': 'value'}}}

        def validate_side_effect():
            """Transition the state to downloaded."""
            raise Exception('Test')

        with patch('processor.report_processor.ReportProcessor._validate_report_details',
                   side_effect=validate_side_effect):
            self.processor.transition_to_new()
            # self.assertEqual(self.report_slice.state, ReportSlice.RETRY_VALIDATION)
            # self.assertEqual(self.report_slice.retry_count, 1)

    def test_transition_to_hosts_uploaded(self):
        """Test the transition to hosts being uploaded."""
        hosts = [{self.uuid: {'bios_uuid': 'value', 'name': 'value'}},
                 {self.uuid2: {'insights_client_id': 'value', 'name': 'foo'}},
                 {self.uuid3: {'ip_addresses': 'value', 'name': 'foo'}},
                 {self.uuid4: {'mac_addresses': 'value', 'name': 'foo'}},
                 {self.uuid5: {'vm_uuid': 'value', 'name': 'foo'}},
                 {self.uuid6: {'etc_machine_id': 'value'}},
                 {self.uuid7: {'subscription_manager_id': 'value'}}]
        self.report_slice.failed_hosts = []
        self.report_slice.candidate_hosts = json.dumps(hosts)
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice
        self.processor.candidate_hosts = hosts
        with patch(
                'processor.report_slice_processor.'
                'ReportSliceProcessor._upload_to_host_inventory',
                return_value=([], [])):
            self.processor.transition_to_hosts_uploaded()
            self.assertEqual(json.loads(self.report_slice.candidate_hosts), [])
            self.assertEqual(self.report_slice.state, ReportSlice.HOSTS_UPLOADED)

    def test_transition_to_hosts_uploaded_unsuccessful(self):
        """Test the transition to hosts being uploaded."""
        hosts = [{self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                  'cause': report_slice_processor.FAILED_UPLOAD,
                  'status_code': '500'},
                 {self.uuid2: {'insights_client_id': 'value', 'name': 'foo'}},
                 {self.uuid3: {'ip_addresses': 'value', 'name': 'foo'}},
                 {self.uuid4: {'mac_addresses': 'value', 'name': 'foo'}},
                 {self.uuid5: {'vm_uuid': 'value', 'name': 'foo'}},
                 {self.uuid6: {'etc_machine_id': 'value'}}]
        retry_commit_hosts = [{self.uuid7: {'subscription_manager_id': 'value'},
                               'cause': report_slice_processor.FAILED_UPLOAD,
                               'status_code': '400'}]
        self.report_slice.failed_hosts = []
        self.report_slice.candidate_hosts = json.dumps(hosts)
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice
        self.processor.candidate_hosts = hosts
        with patch(
                'processor.report_slice_processor.'
                'ReportSliceProcessor._upload_to_host_inventory',
                return_value=(hosts, retry_commit_hosts)):
            self.processor.transition_to_hosts_uploaded()
            total_hosts = hosts + retry_commit_hosts
            for host in total_hosts:
                self.assertIn(host, json.loads(self.report_slice.candidate_hosts))
            self.assertEqual(self.report_slice.state, ReportSlice.VALIDATED)
            self.assertEqual(self.report_slice.retry_count, 1)

    def test_transition_to_hosts_uploaded_no_candidates(self):
        """Test the transition to hosts being uploaded."""
        self.report_record.ready_to_archive = True
        self.report_record.save()
        faulty_report = ReportSlice(
            rh_account='987',
            report_platform_id=self.uuid2,
            report_slice_id=self.uuid,
            state=ReportSlice.NEW,
            report_json=json.dumps(self.report_json),
            state_info=json.dumps([ReportSlice.PENDING, ReportSlice.NEW]),
            last_update_time=datetime.now(pytz.utc),
            candidate_hosts=json.dumps({}),
            failed_hosts=json.dumps([]),
            retry_count=0)
        faulty_report.save()
        self.processor.report_or_slice = faulty_report
        self.processor.account_number = '987'
        self.processor.state = faulty_report.state
        self.processor.report_id = self.uuid2
        self.processor.report_json = self.report_json
        self.processor.candidate_hosts = {}
        self.processor.transition_to_hosts_uploaded()
        # assert the processor was reset
        self.check_variables_are_reset()
        # with self.assertRaises(ReportSlice.DoesNotExist):
        #     ReportSlice.objects.get(id=faulty_report.id)
        # archived = ReportSliceArchive.objects.get(rh_account='987')
        # self.assertEqual(json.loads(archived.state_info),
        #                  [Report.PENDING, Report.NEW, Report.STARTED])

    def test_transition_to_hosts_uploaded_exception(self):
        """Test the transition to hosts being uploaded."""
        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo'},
                 self.uuid3: {'ip_addresses': 'value', 'name': 'foo'},
                 self.uuid4: {'mac_addresses': 'value', 'name': 'foo'},
                 self.uuid5: {'vm_uuid': 'value', 'name': 'foo'},
                 self.uuid6: {'etc_machine_id': 'value'},
                 self.uuid7: {'subscription_manager_id': 'value'}}
        self.processor.candidate_hosts = hosts
        self.processor.report_or_slice = self.report_slice

        def hosts_upload_side_effect():
            raise Exception('Test')

        with patch(
                'processor.report_slice_processor.'
                'ReportSliceProcessor._upload_to_host_inventory',
                side_effect=hosts_upload_side_effect):
            self.processor.transition_to_hosts_uploaded()
            self.assertEqual(self.report_slice.state, Report.VALIDATED)
            self.assertEqual(self.report_slice.retry_count, 1)

    def test_generate_bulk_upload_list(self):
        """Test generating a list of all hosts for upload."""
        self.processor.account_number = self.uuid
        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo'}}
        expected = [{'account': self.uuid, 'display_name': 'value', 'fqdn': 'value',
                     'facts': [{'namespace': 'qpc', 'facts':
                                {'bios_uuid': 'value', 'name': 'value'},
                                'rh_product_certs': [],
                                'rh_products_installed': []}],
                     'bios_uuid': 'value'},
                    {'account': self.uuid, 'display_name': 'foo', 'fqdn': 'foo',
                     'facts': [{'namespace': 'qpc', 'facts':
                                {'insights_client_id': 'value', 'name': 'foo'},
                                'rh_product_certs': [],
                                'rh_products_installed': []}],
                     'insights_client_id': 'value'}]
        list_of_hosts = self.processor.generate_bulk_upload_list(hosts)
        self.assertEqual(list_of_hosts, expected)

    def test_split_host_list(self):
        """Test splitting the host list into."""
        self.processor.account_number = self.uuid
        all_hosts = [{'account': self.uuid, 'display_name': 'value',
                      'fqdn': 'value', 'bios_uuid': 'value',
                      'facts': [{
                          'namespace': 'qpc',
                          'facts': {'bios_uuid': 'value', 'name': 'value'}}]},
                     {'account': self.uuid, 'insights_client_id': 'value',
                      'display_name': 'foo',
                      'fqdn': 'foo', 'facts': [{
                          'namespace': 'qpc',
                          'facts': {'insights_client_id': 'value', 'name': 'foo'}}]}]
        split_hosts = self.processor.split_hosts(all_hosts)
        self.assertEqual(split_hosts, [all_hosts])

    def test_no_account_number_inventory_upload(self):
        """Testing no account number present when uploading to inventory."""
        self.processor.account_number = None
        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo'},
                 self.uuid3: {'ip_addresses': 'value', 'name': 'foo'},
                 self.uuid4: {'mac_addresses': 'value', 'name': 'foo'},
                 self.uuid5: {'vm_uuid': 'value', 'name': 'foo'},
                 self.uuid6: {'etc_machine_id': 'value'},
                 self.uuid7: {'subscription_manager_id': 'value'}}
        self.processor._upload_to_host_inventory(hosts)

    def test_successful_host_inventory_upload(self):
        """Testing successful upload to host inventory."""
        self.processor.account_number = self.uuid
        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value',
                             'infrastructure_type': 'virtualized',
                             'architecture': 'x86',
                             'os_release': 'Red Hat',
                             'cpu_count': 2,
                             'vm_host_core_count': 2,
                             'cpu_socket_count': 5,
                             'vm_host_socket_count': 1,
                             'cpu_core_per_socket': 2,
                             'cpu_core_count': 2},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo'},
                 self.uuid3: {'ip_addresses': 'value', 'name': 'foo'},
                 self.uuid4: {'mac_addresses': 'value', 'name': 'foo'},
                 self.uuid5: {'vm_uuid': 'value', 'name': 'foo'},
                 self.uuid6: {'etc_machine_id': 'value'},
                 self.uuid7: {'subscription_manager_id': 'value'}}
        bulk_response = {
            'errors': 0,
            'total': 7,
            'data': []}
        with requests_mock.mock() as mock_req:
            mock_req.post(report_slice_processor.INSIGHTS_HOST_INVENTORY_URL,
                          status_code=207, json=bulk_response)
            retry_time_hosts, retry_commit_hosts = \
                self.processor._upload_to_host_inventory(hosts)
            self.assertEqual(retry_time_hosts, [])
            self.assertEqual(retry_commit_hosts, [])
            total_hosts = REGISTRY.get_sample_value('valid_hosts_per_report')
            uploaded_hosts = REGISTRY.get_sample_value('hosts_uploaded')
            failed_hosts = REGISTRY.get_sample_value('hosts_failed')
            upload_group_size = REGISTRY.get_sample_value('upload_group_size')
            self.assertEqual(total_hosts, 7)
            self.assertEqual(uploaded_hosts, 7)
            self.assertEqual(failed_hosts, 0)
            self.assertEqual(upload_group_size, 7)

    def test_no_json_resp_host_inventory_upload(self):
        """Testing unsuccessful upload to host inventory."""
        self.processor.account_number = self.uuid
        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo'}}

        expected_hosts = [{self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                           'cause': report_slice_processor.FAILED_UPLOAD},
                          {self.uuid2: {'insights_client_id': 'value', 'name': 'foo'},
                           'cause': report_slice_processor.FAILED_UPLOAD}]
        with requests_mock.mock() as mock_req:
            mock_req.post(report_slice_processor.INSIGHTS_HOST_INVENTORY_URL,
                          status_code=207, json=None)
            retry_time_hosts, retry_commit_hosts = \
                self.processor._upload_to_host_inventory(hosts)
            for host in expected_hosts:
                self.assertIn(host, retry_time_hosts)
            self.assertEqual(retry_commit_hosts, [])
            total_hosts = REGISTRY.get_sample_value('valid_hosts_per_report')
            uploaded_hosts = REGISTRY.get_sample_value('hosts_uploaded')
            failed_hosts = REGISTRY.get_sample_value('hosts_failed')
            upload_group_size = REGISTRY.get_sample_value('upload_group_size')
            self.assertEqual(total_hosts, 2)
            self.assertEqual(uploaded_hosts, 0)
            self.assertEqual(failed_hosts, 2)
            self.assertEqual(upload_group_size, 2)

    def test_400_resp_host_inventory_upload(self):
        """Testing unsuccessful upload to host inventory."""
        self.processor.account_number = self.uuid
        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo'}}

        expected_hosts = [{self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                           'cause': report_slice_processor.FAILED_UPLOAD},
                          {self.uuid2: {'insights_client_id': 'value', 'name': 'foo'},
                           'cause': report_slice_processor.FAILED_UPLOAD}]
        with requests_mock.mock() as mock_req:
            mock_req.post(report_slice_processor.INSIGHTS_HOST_INVENTORY_URL,
                          status_code=400, json=None)
            retry_time_hosts, retry_commit_hosts = \
                self.processor._upload_to_host_inventory(hosts)
            for host in expected_hosts:
                self.assertIn(host, retry_commit_hosts)
            self.assertEqual(retry_time_hosts, [])
            total_hosts = REGISTRY.get_sample_value('valid_hosts_per_report')
            uploaded_hosts = REGISTRY.get_sample_value('hosts_uploaded')
            failed_hosts = REGISTRY.get_sample_value('hosts_failed')
            upload_group_size = REGISTRY.get_sample_value('upload_group_size')
            self.assertEqual(total_hosts, 2)
            self.assertEqual(uploaded_hosts, 0)
            self.assertEqual(failed_hosts, 2)
            self.assertEqual(upload_group_size, 2)

    def test_500_resp_host_inventory_upload(self):
        """Testing unsuccessful upload to host inventory."""
        self.processor.account_number = self.uuid
        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo'}}

        expected_hosts = [{self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                           'cause': report_slice_processor.FAILED_UPLOAD},
                          {self.uuid2: {'insights_client_id': 'value', 'name': 'foo'},
                           'cause': report_slice_processor.FAILED_UPLOAD}]
        with requests_mock.mock() as mock_req:
            mock_req.post(report_slice_processor.INSIGHTS_HOST_INVENTORY_URL,
                          status_code=500, json=None)
            retry_time_hosts, retry_commit_hosts = \
                self.processor._upload_to_host_inventory(hosts)
            for host in expected_hosts:
                self.assertIn(host, retry_time_hosts)
            self.assertEqual(retry_commit_hosts, [])
            total_hosts = REGISTRY.get_sample_value('valid_hosts_per_report')
            uploaded_hosts = REGISTRY.get_sample_value('hosts_uploaded')
            failed_hosts = REGISTRY.get_sample_value('hosts_failed')
            upload_group_size = REGISTRY.get_sample_value('upload_group_size')
            self.assertEqual(total_hosts, 2)
            self.assertEqual(uploaded_hosts, 0)
            self.assertEqual(failed_hosts, 2)
            self.assertEqual(upload_group_size, 2)

    def test_host_inventory_upload_500(self):
        """Testing successful upload to host inventory with 500 errors."""
        self.processor.account_number = self.uuid
        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value',
                             'system_platform_id': self.uuid},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo',
                              'system_platform_id': self.uuid2}}
        expected_hosts = [{self.uuid: {'bios_uuid': 'value', 'name': 'value',
                                       'system_platform_id': self.uuid},
                           'cause': report_slice_processor.FAILED_UPLOAD,
                           'status_code': 500},
                          {self.uuid2: {'insights_client_id': 'value', 'name': 'foo',
                                        'system_platform_id': self.uuid2},
                           'cause': report_slice_processor.FAILED_UPLOAD,
                           'status_code': 500}]
        bulk_response = {
            'errors': 2,
            'total': 2,
            'data': [
                {'status': 500,
                 'host': {'facts': [{'namespace': 'qpc', 'facts':
                                     {'system_platform_id': self.uuid}}]}},
                {'status': 500,
                 'host': {'facts': [{'namespace': 'qpc', 'facts':
                                     {'system_platform_id': self.uuid2}}]}}]}
        with requests_mock.mock() as mock_req:
            mock_req.post(report_slice_processor.INSIGHTS_HOST_INVENTORY_URL,
                          status_code=207, json=bulk_response)
            retry_time, retry_commit = self.processor._upload_to_host_inventory(hosts)
            self.assertEqual(retry_commit, [])
            for host in expected_hosts:
                self.assertIn(host, retry_time)
            total_hosts = REGISTRY.get_sample_value('valid_hosts_per_report')
            uploaded_hosts = REGISTRY.get_sample_value('hosts_uploaded')
            failed_hosts = REGISTRY.get_sample_value('hosts_failed')
            upload_group_size = REGISTRY.get_sample_value('upload_group_size')
            self.assertEqual(total_hosts, 2)
            self.assertEqual(uploaded_hosts, 0)
            self.assertEqual(failed_hosts, 2)
            self.assertEqual(upload_group_size, 2)

    def test_host_inventory_upload_400(self):
        """Testing successful upload to host inventory with 500 errors."""
        self.processor.account_number = self.uuid
        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value',
                             'system_platform_id': self.uuid},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo',
                              'system_platform_id': self.uuid2},
                 self.uuid3: {'ip_addresses': 'value', 'name': 'foo'},
                 self.uuid4: {'mac_addresses': 'value', 'name': 'foo'},
                 self.uuid5: {'vm_uuid': 'value', 'name': 'foo'},
                 self.uuid6: {'etc_machine_id': 'value'},
                 self.uuid7: {'subscription_manager_id': 'value'}}
        expected_hosts = [{self.uuid: {'bios_uuid': 'value', 'name': 'value',
                                       'system_platform_id': self.uuid},
                           'cause': report_slice_processor.FAILED_UPLOAD,
                           'status_code': 400},
                          {self.uuid2: {'insights_client_id': 'value', 'name': 'foo',
                                        'system_platform_id': self.uuid2},
                           'cause': report_slice_processor.FAILED_UPLOAD,
                           'status_code': 400}]
        bulk_response = {
            'errors': 2,
            'total': 7,
            'data': [
                {'status': 400,
                 'host': {'facts': [{'namespace': 'qpc', 'facts':
                                     {'system_platform_id': self.uuid}}]}},
                {'status': 400,
                 'host': {'facts': [{'namespace': 'qpc', 'facts':
                                     {'system_platform_id': self.uuid2}}]}}]}
        with requests_mock.mock() as mock_req:
            mock_req.post(report_slice_processor.INSIGHTS_HOST_INVENTORY_URL,
                          status_code=207, json=bulk_response)
            retry_time_hosts, retry_commit_hosts = \
                self.processor._upload_to_host_inventory(hosts)
            self.assertEqual(retry_time_hosts, [])
            for host in expected_hosts:
                self.assertIn(host, retry_commit_hosts)
            total_hosts = REGISTRY.get_sample_value('valid_hosts_per_report')
            uploaded_hosts = REGISTRY.get_sample_value('hosts_uploaded')
            failed_hosts = REGISTRY.get_sample_value('hosts_failed')
            upload_group_size = REGISTRY.get_sample_value('upload_group_size')
            self.assertEqual(total_hosts, 7)
            self.assertEqual(uploaded_hosts, 5)
            self.assertEqual(failed_hosts, 2)
            self.assertEqual(upload_group_size, 7)

    @patch('processor.report_processor.requests.post')
    def test_host_url_exceptions(self, mock_request):
        """Testing an exception being raised during host inventory upload."""
        good_resp = requests.Response()
        good_resp.status_code = 200
        bad_resp = requests.exceptions.ConnectionError()
        mock_request.side_effect = [good_resp, bad_resp]
        self.processor.account_number = '00001'
        self.processor.report_id = '0001-kevan'
        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo'}}
        with patch('processor.report_slice_processor.INSIGHTS_HOST_INVENTORY_URL',
                   value='not none'):
            self.processor._upload_to_host_inventory(hosts)

    def test_format_certs(self):
        """Testing the format_certs function."""
        certs = ['69.pem', '67.pem', '']
        formatted_certs = self.processor.format_certs(certs)
        self.assertEqual([69, 67], formatted_certs)
        # assert empty list stays empty
        certs = []
        formatted_certs = self.processor.format_certs(certs)
        self.assertEqual([], formatted_certs)
        # assert exception returns empty
        certs = ['notint.pem']
        formatted_certs = self.processor.format_certs(certs)
        self.assertEqual([], formatted_certs)

    def test_format_products(self):
        """Testing the format_prodcuts function."""
        products = [
            {'name': 'JBoss EAP',
             'presence': 'present'},
            {'name': 'JBoss Fuse',
             'presence': 'present'},
            {'name': 'JBoss BRMS',
             'presence': 'absent'},
            {'name': 'JBoss Web Server',
             'presence': 'absent'}
        ]
        is_rhel = True
        formatted_products = self.processor.format_products(products, is_rhel)
        expected = ['RHEL', 'EAP', 'FUSE']
        self.assertEqual(expected, formatted_products)
        # test no products
        products = []
        is_rhel = None
        formatted_products = self.processor.format_products(products, is_rhel)
        self.assertEqual([], formatted_products)

    def test_system_profile_basic(self):
        """Testing the system profile function."""
        host = {'infrastructure_type': 'virtualized',
                'architecture': 'x86',
                'os_release': 'Red Hat',
                'cpu_count': 2,
                'vm_host_core_count': 2,
                'cpu_socket_count': 5,
                'vm_host_socket_count': 1,
                'cpu_core_per_socket': 2,
                'cpu_core_count': 5}
        system_profile = self.processor.format_system_profile(host)
        expected_profile = {'infrastructure_type': 'virtualized',
                            'arch': 'x86',
                            'os_release': 'Red Hat',
                            'number_of_cpus': 2,
                            'number_of_sockets': 1,
                            'cores_per_socket': 2}
        self.assertEqual(expected_profile, system_profile)
        # test cores_per_socket can be calculated from vm vals
        host.pop('cpu_core_per_socket')
        system_profile = self.processor.format_system_profile(host)
        self.assertEqual(expected_profile, system_profile)
        # test cores_per_socket is calculated with mixed vals
        host.pop('vm_host_core_count')
        system_profile = self.processor.format_system_profile(host)
        expected_profile['cores_per_socket'] = 5
        self.assertEqual(expected_profile, system_profile)
        # test more mixed values
        host.pop('vm_host_socket_count')
        host['vm_host_core_count'] = 9
        expected_profile['number_of_sockets'] = 5
        expected_profile['cores_per_socket'] = 2
        system_profile = self.processor.format_system_profile(host)
        self.assertEqual(expected_profile, system_profile)
        # test system profile with non vm values
        host.pop('vm_host_core_count')
        expected_profile['cores_per_socket'] = 1
        system_profile = self.processor.format_system_profile(host)
        self.assertEqual(expected_profile, system_profile)
