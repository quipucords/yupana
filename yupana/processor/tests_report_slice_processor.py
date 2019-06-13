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
from asynctest import CoroutineMock
from django.test import TestCase
from processor import (abstract_processor,
                       kafka_msg_handler as msg_handler,
                       report_slice_processor,
                       tests_kafka_msg_handler as test_handler)
from prometheus_client import REGISTRY

from api.models import (Report,
                        ReportArchive,
                        ReportSlice,
                        ReportSliceArchive)


# pylint: disable=too-many-public-methods
# pylint: disable=protected-access,too-many-lines,too-many-instance-attributes
class ReportProcessorTests(TestCase):
    """Test Cases for the Message processor."""

    def setUp(self):
        """Create test setup."""
        self.payload_url = 'http://insights-upload.com/q/file_to_validate'
        self.uuid = uuid.uuid4()
        self.uuid2 = uuid.uuid4()
        self.uuid3 = uuid.uuid4()
        self.uuid4 = uuid.uuid4()
        self.uuid5 = uuid.uuid4()
        self.uuid6 = uuid.uuid4()
        self.uuid7 = uuid.uuid4()
        self.fake_record = test_handler.KafkaMsg(msg_handler.QPC_TOPIC, 'http://internet.com')
        self.msg = msg_handler.unpack_consumer_record(self.fake_record)
        self.report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': [{'bios_uuid': 'value'},
                      {'invalid': 'value'}]}
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
            state_info=json.dumps([ReportSlice.NEW]),
            retry_count=0,
            last_update_time=datetime.now(pytz.utc),
            failed_hosts=[],
            candidate_hosts=[],
            report=self.report_record,
            ready_to_archive=True,
            hosts_count=2)
        self.report_slice.save()
        self.report_record.save()
        self.processor = report_slice_processor.ReportSliceProcessor()
        self.processor.report = self.report_slice

    def check_variables_are_reset(self):
        """Check that report processor members have been cleared."""
        processor_attributes = [self.processor.report_platform_id,
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
        self.report_slice.state = ReportSlice.VALIDATED
        self.report_slice.report_platform_id = self.uuid
        self.report_slice.candidate_hosts = json.dumps([
            {str(self.uuid3): {'ip_addresses': 'value', 'name': 'value'},
             'cause': report_slice_processor.FAILED_UPLOAD}])
        self.report_slice.failed_hosts = json.dumps(
            [{str(self.uuid2): {'ip_addresses': 'value', 'name': 'value'},
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

        # test pending state for delegate
        self.report_slice.state = ReportSlice.PENDING
        self.processor.report_or_slice = self.report_slice
        await self.processor.delegate_state()
        self.check_variables_are_reset()

    def test_run_delegate(self):
        """Test the async function delegate state."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_delegate_state)
        event_loop.run_until_complete(coro())
        event_loop.close()

    def test_update_slice_state(self):
        """Test updating the slice state."""
        self.report_slice.failed_hosts = json.dumps([])
        self.report_slice.save()
        report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {str(self.uuid): {'key': 'value'}}}
        failed_hosts = [{str(self.uuid6): {'etc_machine_id': 'value'}},
                        {str(self.uuid7): {'subscription_manager_id': 'value'}}]
        self.processor.report_or_slice = self.report_slice
        self.processor.next_state = ReportSlice.VALIDATED
        options = {'report_json': report_json,
                   'failed_hosts': failed_hosts}
        self.processor.update_object_state(options=options)
        self.assertEqual(json.loads(self.report_slice.report_json), report_json)
        self.assertEqual(json.loads(self.report_slice.failed_hosts), failed_hosts)

    def test_transition_to_validated_general_exception(self):
        """Test that when a general exception is raised, we don't pass validation."""
        self.report_slice.state = ReportSlice.RETRY_VALIDATION
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice

        def validate_side_effect():
            """Transition the state to downloaded."""
            raise Exception('Test')

        with patch('processor.report_slice_processor.ReportSliceProcessor._validate_report_details',
                   side_effect=validate_side_effect):
            self.processor.transition_to_validated()
            self.assertEqual(self.report_slice.state, ReportSlice.RETRY_VALIDATION)
            self.assertEqual(self.report_slice.retry_count, 1)

    def test_transition_to_validated(self):
        """Test that when a general exception is raised, we don't pass validation."""
        self.report_slice.state = ReportSlice.RETRY_VALIDATION
        report_json = {
            'report_slice_id': '384794738',
            'hosts': [{'ip_addresses': 'value'}]}
        self.report_slice.report_json = json.dumps(report_json)
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice
        self.processor.transition_to_validated()
        self.assertEqual(self.report_slice.state, ReportSlice.VALIDATED)
        self.assertEqual(self.report_slice.retry_count, 0)

    def test_transition_to_validated_failed(self):
        """Test report missing slice id."""
        self.report_slice.state = ReportSlice.RETRY_VALIDATION
        report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {str(self.uuid): {'ip_addresses': 'value'}}}
        self.report_slice.report_json = json.dumps(report_json)
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice
        self.processor.transition_to_validated()
        self.assertEqual(self.report_slice.state, ReportSlice.FAILED_VALIDATION)
        self.assertEqual(self.report_slice.retry_count, 0)
        self.assertEqual(self.report_slice.ready_to_archive, True)

    def test_moved_candidates_to_failed(self):
        """Test that we reset candidates after moving them to failed."""
        candidates = [{self.uuid: {'bios_uuid': 'value', 'name': 'value'}}]
        self.processor.candidate_hosts = candidates
        self.processor.failed_hosts = [
            {self.uuid2: {'bios_uuid': 'value', 'name': 'value'},
             'cause': abstract_processor.FAILED_VALIDATION}]
        self.processor.move_candidates_to_failed()
        self.assertEqual(self.processor.candidate_hosts, [])
        for host in candidates:
            self.assertIn(host, self.processor.failed_hosts)

    def test_determine_retry_limit(self):
        """Test the determine retry method when the retry is at the limit."""
        candidates = [{str(self.uuid3): {'ip_addresses': 'value', 'name': 'value'},
                       'cause': report_slice_processor.FAILED_UPLOAD}]
        self.report_slice.state = ReportSlice.VALIDATED
        self.report_slice.retry_count = 4
        self.report_slice.candidate_hosts = json.dumps(candidates)
        self.report_slice.failed_hosts = json.dumps([])
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice
        self.processor.candidate_hosts = candidates
        self.processor.failed_hosts = []
        self.processor.determine_retry(ReportSlice.FAILED_HOSTS_UPLOAD,
                                       ReportSlice.VALIDATED)
        self.assertEqual(self.report_slice.state, ReportSlice.FAILED_HOSTS_UPLOAD)
        self.assertEqual(json.loads(self.report_slice.candidate_hosts), [])
        for host in candidates:
            self.assertIn(host, json.loads(self.report_slice.failed_hosts))

    async def async_test_transition_to_hosts_uploaded(self):
        """Test the transition to hosts being uploaded."""
        hosts = [{str(self.uuid): {'bios_uuid': 'value', 'name': 'value',
                                   'system_platform_id': str(self.uuid)}},
                 {str(self.uuid2): {'insights_client_id': 'value', 'name': 'foo',
                                    'system_platform_id': str(self.uuid2)}},
                 {str(self.uuid3): {'ip_addresses': 'value', 'name': 'foo',
                                    'system_platform_id': str(self.uuid3)}},
                 {str(self.uuid4): {'mac_addresses': 'value', 'name': 'foo',
                                    'system_platform_id': str(self.uuid4)}},
                 {str(self.uuid5): {'vm_uuid': 'value', 'name': 'foo',
                                    'system_platform_id': str(self.uuid5)}},
                 {str(self.uuid6): {'etc_machine_id': 'value',
                                    'system_platform_id': str(self.uuid6)}},
                 {str(self.uuid7): {'subscription_manager_id': 'value',
                                    'system_platform_id': str(self.uuid7)}}]
        self.report_slice.failed_hosts = []
        self.report_slice.candidate_hosts = json.dumps(hosts)
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice
        self.processor.candidate_hosts = hosts
        self.processor._upload_to_host_inventory = CoroutineMock(
            return_value=([], []))
        await self.processor.transition_to_hosts_uploaded()
        self.assertEqual(json.loads(self.report_slice.candidate_hosts), [])
        self.assertEqual(self.report_slice.state, ReportSlice.HOSTS_UPLOADED)

    def test_transition_to_hosts_uploaded(self):
        """Test the async hosts uploaded successful."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_transition_to_hosts_uploaded)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_test_transition_to_hosts_uploaded_unsuccessful(self):
        """Test the transition to hosts being uploaded."""
        hosts = [{str(self.uuid): {'bios_uuid': 'value', 'name': 'value'},
                  'cause': report_slice_processor.FAILED_UPLOAD,
                  'status_code': '500'},
                 {str(self.uuid2): {'insights_client_id': 'value', 'name': 'foo'}},
                 {str(self.uuid3): {'ip_addresses': 'value', 'name': 'foo'}},
                 {str(self.uuid4): {'mac_addresses': 'value', 'name': 'foo'}},
                 {str(self.uuid5): {'vm_uuid': 'value', 'name': 'foo'}},
                 {str(self.uuid6): {'etc_machine_id': 'value'}}]
        retry_commit_hosts = [{str(self.uuid7): {'subscription_manager_id': 'value'},
                               'cause': report_slice_processor.FAILED_UPLOAD,
                               'status_code': '400'}]
        self.report_slice.failed_hosts = []
        self.report_slice.candidate_hosts = json.dumps(hosts)
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice
        self.processor.candidate_hosts = hosts
        self.processor._upload_to_host_inventory = CoroutineMock(
            return_value=(hosts, retry_commit_hosts))
        await self.processor.transition_to_hosts_uploaded()
        total_hosts = hosts + retry_commit_hosts
        for host in total_hosts:
            self.assertIn(host,
                          json.loads(self.report_slice.candidate_hosts))
        self.assertEqual(self.report_slice.state, ReportSlice.VALIDATED)
        self.assertEqual(self.report_slice.retry_count, 1)

    def test_transition_to_hosts_uploaded_unsuccessful(self):
        """Test the async hosts uploaded unsuccessful."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_transition_to_hosts_uploaded_unsuccessful)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_test_transition_to_hosts_uploaded_no_candidates(self):
        """Test the transition to hosts being uploaded."""
        self.report_record.ready_to_archive = True
        self.report_record.save()
        faulty_report = ReportSlice(
            rh_account='987',
            report_platform_id=str(self.uuid2),
            report_slice_id=str(self.uuid),
            state=ReportSlice.NEW,
            report_json=json.dumps(self.report_json),
            state_info=json.dumps([ReportSlice.PENDING, ReportSlice.NEW]),
            last_update_time=datetime.now(pytz.utc),
            candidate_hosts=json.dumps({}),
            failed_hosts=json.dumps([]),
            hosts_count=10,
            retry_count=0)
        faulty_report.save()
        self.processor.report_or_slice = faulty_report
        self.processor.account_number = '987'
        self.processor.state = faulty_report.state
        self.processor.report_platform_id = self.uuid2
        self.processor.report_json = self.report_json
        self.processor.candidate_hosts = {}
        await self.processor.transition_to_hosts_uploaded()
        # assert the processor was reset
        self.check_variables_are_reset()

    def test_test_transition_to_hosts_uploaded_no_candidates(self):
        """Test the async hosts uploaded no candidates."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_transition_to_hosts_uploaded_no_candidates)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_test_transition_to_hosts_uploaded_exception(self):
        """Test the transition to hosts being uploaded."""
        hosts = {str(self.uuid): {'bios_uuid': 'value', 'name': 'value'},
                 str(self.uuid2): {'insights_client_id': 'value', 'name': 'foo'},
                 str(self.uuid3): {'ip_addresses': 'value', 'name': 'foo'},
                 str(self.uuid4): {'mac_addresses': 'value', 'name': 'foo'},
                 str(self.uuid5): {'vm_uuid': 'value', 'name': 'foo'},
                 str(self.uuid6): {'etc_machine_id': 'value'},
                 str(self.uuid7): {'subscription_manager_id': 'value'}}
        self.processor.candidate_hosts = hosts
        self.processor.report_or_slice = self.report_slice

        def hosts_upload_side_effect():
            raise Exception('Test')

        with patch(
                'processor.report_slice_processor.'
                'ReportSliceProcessor._upload_to_host_inventory',
                side_effect=hosts_upload_side_effect):
            await self.processor.transition_to_hosts_uploaded()
            self.assertEqual(self.report_slice.state, Report.VALIDATED)
            self.assertEqual(self.report_slice.retry_count, 1)

    def test_test_transition_to_hosts_uploaded_exception(self):
        """Test the async hosts uploaded exception."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_transition_to_hosts_uploaded_exception)
        event_loop.run_until_complete(coro())
        event_loop.close()

    def test_generate_bulk_upload_list(self):
        """Test generating a list of all hosts for upload."""
        hosts = {self.uuid: {'account': self.uuid3, 'bios_uuid': 'value', 'display_name': 'value',
                             'facts': [{'namespace': 'yupana', 'facts':
                                        {'yupana_host_id': self.uuid,
                                         'bios_uuid': 'value', 'name': 'value'},
                                        'rh_product_certs': [],
                                        'rh_products_installed': []}]},
                 self.uuid2: {'account': self.uuid3,
                              'insights_client_id': 'value', 'display_name': 'foo',
                              'facts': [{'namespace': 'yupana',
                                         'facts': {'yupana_host_id': self.uuid2,
                                                   'insights_client_id': 'value',
                                                   'name': 'foo'},
                                         'rh_product_certs': [],
                                         'rh_products_installed': []}]}}
        expected = [{'account': self.uuid3,
                     'bios_uuid': 'value',
                     'display_name': 'value',
                     'facts': [{'namespace': 'yupana',
                                'facts': {'yupana_host_id': self.uuid,
                                          'bios_uuid': 'value',
                                          'name': 'value'},
                                'rh_product_certs': [],
                                'rh_products_installed': []}]},
                    {'account': self.uuid3,
                     'insights_client_id': 'value',
                     'display_name': 'foo',
                     'facts': [{'namespace': 'yupana',
                                'facts': {'yupana_host_id': self.uuid2,
                                          'insights_client_id': 'value',
                                          'name': 'foo'},
                                'rh_product_certs': [],
                                'rh_products_installed': []}]}]
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
        self.assertEqual(split_hosts, [[all_hosts]])

    async def async_test_no_account_number_inventory_upload(self):
        """Test the no account number present when uploading to inventory."""
        self.processor.account_number = None
        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo'},
                 self.uuid3: {'ip_addresses': 'value', 'name': 'foo'},
                 self.uuid4: {'mac_addresses': 'value', 'name': 'foo'},
                 self.uuid5: {'vm_uuid': 'value', 'name': 'foo'},
                 self.uuid6: {'etc_machine_id': 'value'},
                 self.uuid7: {'subscription_manager_id': 'value'}}
        await self.processor._upload_to_host_inventory(hosts)

    def test_no_account_number_inventory_upload(self):
        """Test the async upload function with a 400 error."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_no_account_number_inventory_upload)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_test_successful_host_inventory_upload(self):
        """Test successful upload to host inventory."""
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
                await self.processor._upload_to_host_inventory(hosts)
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

    def test_successful_host_inventory_upload(self):
        """Test the async upload function with a 400 error."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_successful_host_inventory_upload)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_test_no_json_resp_host_inventory_upload(self):
        """Test unsuccessful upload to host inventory."""
        self.processor.account_number = self.uuid
        hosts = {str(self.uuid): {'bios_uuid': 'value', 'name': 'value',
                                  'facts': [{'namespace': 'yupana',
                                             'facts': {'yupana_host_id': str(self.uuid)}}]},
                 str(self.uuid2): {'insights_client_id':
                                   'value', 'name': 'foo',
                                   'facts': [{'namespace': 'yupana',
                                              'facts': {'yupana_host_id': str(self.uuid2)}}]}}

        expected_hosts = [{str(self.uuid): {'bios_uuid': 'value', 'name': 'value',
                                            'facts': [{'namespace': 'yupana',
                                                       'facts': {'yupana_host_id':
                                                                 str(self.uuid)}}]},
                           'cause': report_slice_processor.FAILED_UPLOAD},
                          {str(self.uuid2): {'insights_client_id': 'value', 'name': 'foo',
                                             'facts': [{'namespace': 'yupana',
                                                        'facts': {'yupana_host_id':
                                                                  str(self.uuid2)}}]},
                           'cause': report_slice_processor.FAILED_UPLOAD}]
        with requests_mock.mock() as mock_req:
            mock_req.post(report_slice_processor.INSIGHTS_HOST_INVENTORY_URL,
                          status_code=207, json=None)
            retry_time_hosts, retry_commit_hosts = \
                await self.processor._upload_to_host_inventory(hosts)
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

    def test_no_json_resp_host_inventory_upload(self):
        """Test the async upload function with a 400 error."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_no_json_resp_host_inventory_upload)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_test_400_resp_host_inventory_upload(self):
        """Test 400 response when uploading to host inventory."""
        self.processor.account_number = self.uuid
        hosts = {str(self.uuid):
                 {'bios_uuid': 'value', 'display_name': 'value',
                  'facts': [{'namespace': 'yupana',
                             'facts': {'yupana_host_id': str(self.uuid)}}]},
                 str(self.uuid2):
                 {'insights_client_id': 'value', 'display_name': 'foo',
                  'facts': [{'namespace': 'yupana',
                             'facts': {'yupana_host_id': str(self.uuid2)}}]}}

        expected_hosts = [{str(self.uuid): {'bios_uuid': 'value', 'display_name': 'value',
                                            'facts': [{'namespace': 'yupana',
                                                       'facts': {'yupana_host_id':
                                                                 str(self.uuid)}
                                                       }]},
                           'cause': report_slice_processor.FAILED_UPLOAD},
                          {str(self.uuid2): {'insights_client_id': 'value', 'display_name': 'foo',
                                             'facts': [{'namespace': 'yupana',
                                                        'facts': {'yupana_host_id':
                                                                  str(self.uuid2)}}]},
                           'cause': report_slice_processor.FAILED_UPLOAD}]
        with requests_mock.mock() as mock_req:
            mock_req.post(report_slice_processor.INSIGHTS_HOST_INVENTORY_URL,
                          status_code=400, json=None)
            retry_time_hosts, retry_commit_hosts = \
                await self.processor._upload_to_host_inventory(hosts)
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

    def test_400_resp_host_inventory_upload(self):
        """Test the async upload function with a 400 error."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_400_resp_host_inventory_upload)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_test_500_resp_host_inventory_upload(self):
        """Test 500 response when uploading to host inventory."""
        self.processor.account_number = self.uuid
        hosts = {
            str(self.uuid): {
                'bios_uuid': 'value', 'display_name': 'value',
                'facts': [{'namespace': 'yupana',
                           'facts': {'yupana_host_id': str(self.uuid)}}]},
            str(self.uuid2): {
                'insights_client_id': 'value',
                'display_name': 'foo',
                'facts': [{'namespace': 'yupana', 'facts':
                           {'yupana_host_id': str(self.uuid2)}}]}}

        expected_hosts = [{str(self.uuid): {'bios_uuid': 'value', 'display_name': 'value',
                                            'facts': [{'namespace': 'yupana',
                                                       'facts':
                                                       {'yupana_host_id': str(self.uuid)}}]},
                           'cause': report_slice_processor.FAILED_UPLOAD},
                          {str(self.uuid2): {'insights_client_id': 'value', 'display_name': 'foo',
                                             'facts': [{'namespace': 'yupana',
                                                        'facts': {'yupana_host_id':
                                                                  str(self.uuid2)}}]},
                           'cause': report_slice_processor.FAILED_UPLOAD}]
        with requests_mock.mock() as mock_req:
            mock_req.post(report_slice_processor.INSIGHTS_HOST_INVENTORY_URL,
                          status_code=500, json=None)
            retry_time_hosts, retry_commit_hosts = \
                await self.processor._upload_to_host_inventory(hosts)
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

    def test_500_resp_host_inventory_upload(self):
        """Test the async upload function with a 400 error."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_500_resp_host_inventory_upload)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_test_host_inventory_upload_500(self):
        """Test unsuccessful upload to host inventory with 500 errors."""
        self.processor.account_number = self.uuid
        hosts = {str(self.uuid): {'bios_uuid': 'value', 'name': 'value',
                                  'facts': [{'namespace': 'yupana',
                                             'facts': {'yupana_host_id': str(self.uuid)}}]},
                 str(self.uuid2): {'insights_client_id': 'value', 'name': 'foo',
                                   'facts': [{'namespace': 'yupana',
                                              'facts': {'yupana_host_id': str(self.uuid2)}}]}}
        expected_hosts = [{str(self.uuid): {'bios_uuid': 'value', 'name': 'value',
                                            'facts': [{'namespace': 'yupana',
                                                       'facts': {'yupana_host_id':
                                                                 str(self.uuid)}}]},
                           'cause': report_slice_processor.FAILED_UPLOAD,
                           'status_code': 500},
                          {str(self.uuid2): {'insights_client_id': 'value', 'name': 'foo',
                                             'facts': [{'namespace': 'yupana',
                                                        'facts': {'yupana_host_id':
                                                                  str(self.uuid2)}}]},
                           'cause': report_slice_processor.FAILED_UPLOAD,
                           'status_code': 500}]
        bulk_response = {
            'errors': 2,
            'total': 2,
            'data': [
                {'status': 500,
                 'host': {'facts': [{'namespace': 'yupana', 'facts':
                                     {'yupana_host_id': str(self.uuid)}}]}},
                {'status': 500,
                 'host': {'facts': [{'namespace': 'yupana', 'facts':
                                     {'yupana_host_id': str(self.uuid2)}}]}}]}
        with requests_mock.mock() as mock_req:
            mock_req.post(report_slice_processor.INSIGHTS_HOST_INVENTORY_URL,
                          status_code=207, json=bulk_response)
            retry_time, retry_commit = \
                await self.processor._upload_to_host_inventory(hosts)
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

    def test_host_inventory_upload_500(self):
        """Test the async upload function with a 400 error."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_host_inventory_upload_500)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_test_host_inventory_upload_400(self):
        """Test upload to host inventory with 400 errors."""
        self.processor.account_number = self.uuid
        hosts = {str(self.uuid): {'bios_uuid': 'value', 'name': 'value',
                                  'facts': [{'namespace': 'yupana',
                                             'facts': {'yupana_host_id': str(self.uuid)}}]},
                 str(self.uuid2): {'insights_client_id': 'value', 'name': 'foo',
                                   'facts': [{'namespace': 'yupana',
                                              'facts': {'yupana_host_id': str(self.uuid2)}}]},
                 str(self.uuid3): {'ip_addresses': 'value', 'name': 'foo',
                                   'facts': [{'namespace': 'yupana',
                                              'facts': {'yupana_host_id': str(self.uuid3)}}]},
                 self.uuid4: {'mac_addresses': 'value', 'name': 'foo',
                              'facts': [{'namespace': 'yupana',
                                         'facts': {'yupana_host_id': str(self.uuid4)}}]},
                 str(self.uuid5): {'vm_uuid': 'value', 'name': 'foo',
                                   'facts': [{'namespace': 'yupana',
                                              'facts': {'yupana_host_id': str(self.uuid5)}}]},
                 str(self.uuid6): {'etc_machine_id': 'value',
                                   'facts': [{'namespace': 'yupana',
                                              'facts': {'yupana_host_id': str(self.uuid6)}}]},
                 str(self.uuid7): {'subscription_manager_id': 'value',
                                   'facts': [{'namespace': 'yupana',
                                              'facts': {'yupana_host_id': str(self.uuid7)}}]}}
        expected_hosts = [{str(self.uuid): {'bios_uuid': 'value', 'name': 'value',
                                            'facts': [{'namespace': 'yupana',
                                                       'facts': {'yupana_host_id':
                                                                 str(self.uuid)}}]},
                           'cause': report_slice_processor.FAILED_UPLOAD,
                           'status_code': 400},
                          {str(self.uuid2): {'insights_client_id': 'value', 'name': 'foo',
                                             'facts': [{'namespace': 'yupana',
                                                        'facts': {'yupana_host_id':
                                                                  str(self.uuid2)}}]},
                           'cause': report_slice_processor.FAILED_UPLOAD,
                           'status_code': 400}]
        bulk_response = {
            'errors': 2,
            'total': 7,
            'data': [
                {'status': 400,
                 'host': {'facts': [{'namespace': 'yupana', 'facts':
                                     {'yupana_host_id': str(self.uuid)}}]}},
                {'status': 400,
                 'host': {'facts': [{'namespace': 'yupana', 'facts':
                                     {'yupana_host_id': str(self.uuid2)}}]}}]}
        with requests_mock.mock() as mock_req:
            mock_req.post(report_slice_processor.INSIGHTS_HOST_INVENTORY_URL,
                          status_code=207, json=bulk_response)
            retry_time_hosts, retry_commit_hosts = \
                await self.processor._upload_to_host_inventory(hosts)
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

    def test_upload_400(self):
        """Test the async upload function with a 400 error."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_host_inventory_upload_400)
        event_loop.run_until_complete(coro())
        event_loop.close()

    @patch('processor.report_processor.requests.post')
    async def async_test_host_url_exceptions(self, mock_request):
        """Test an exception being raised during host inventory upload."""
        good_resp = requests.Response()
        good_resp.status_code = 200
        bad_resp = requests.exceptions.ConnectionError()
        mock_request.side_effect = [good_resp, bad_resp]
        self.processor.account_number = '00001'
        self.processor.report_platform_id = '0001-kevan'
        hosts = {str(self.uuid): {'bios_uuid': 'value', 'name': 'value'},
                 str(self.uuid2): {'insights_client_id': 'value', 'name': 'foo'}}
        with patch('processor.report_slice_processor.INSIGHTS_HOST_INVENTORY_URL',
                   value='not none'):
            await self.processor._upload_to_host_inventory(hosts)

    def test_upload_host_url_exceptions(self):
        """Test the async upload function with url exceptions."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_host_url_exceptions)
        event_loop.run_until_complete(coro())
        event_loop.close()

    @patch('processor.report_processor.requests.post')
    async def async_test_host_url_request_exceptions(self, mock_request):
        """Test a request exception raised during host inventory upload."""
        mock_request.side_effect = requests.exceptions.RequestException()
        self.processor.account_number = '00001'
        self.processor.report_platform_id = '0001-kevan'
        hosts = {str(self.uuid): {'bios_uuid': 'value', 'name': 'value'},
                 str(self.uuid2): {'insights_client_id': 'value', 'name': 'foo'}}
        await self.processor._upload_to_host_inventory(hosts)

    def test_host_url_request_exceptions(self):
        """Test the async upload function with url exceptions."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_host_url_request_exceptions)
        event_loop.run_until_complete(coro())
        event_loop.close()

    def test_archive_report_and_slices_in_failed_state(self):
        """Test the archive method in a failed state."""
        self.report_record.ready_to_archive = True
        self.report_record.report_platform_id = str(self.uuid)
        self.report_record.save()
        self.report_slice.ready_to_archive = True
        self.report_slice.report_platform_id = str(self.uuid)
        self.report_slice.report_slice_id = str(self.uuid2)
        self.report_slice.state = ReportSlice.FAILED_HOSTS_UPLOAD
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice
        self.processor.report_platform_id = str(self.uuid)

        self.processor.archive_report_and_slices()
        # assert the report doesn't exist
        with self.assertRaises(Report.DoesNotExist):
            Report.objects.get(id=self.report_record.id)
        # assert the report archive does exist
        archived = ReportArchive.objects.get(rh_account=self.report_record.rh_account)
        archived_slice = ReportSliceArchive.objects.get(
            report_slice_id=self.report_slice.report_slice_id)
        self.assertEqual(str(archived.report_platform_id), str(self.uuid))
        self.assertEqual(str(archived_slice.report_platform_id), str(self.uuid))
        # assert the processor was reset
        self.check_variables_are_reset()

    def test_archive_report_and_slices_in_success_state(self):
        """Test the archive method in a failed state."""
        self.report_record.ready_to_archive = True
        self.report_record.report_platform_id = str(self.uuid)
        self.report_record.save()
        self.report_slice.ready_to_archive = True
        self.report_slice.report_platform_id = str(self.uuid)
        self.report_slice.report_slice_id = str(self.uuid2)
        self.report_slice.state = ReportSlice.HOSTS_UPLOADED
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice
        self.processor.report_platform_id = str(self.uuid)

        self.processor.archive_report_and_slices()
        # assert the report doesn't exist
        with self.assertRaises(Report.DoesNotExist):
            Report.objects.get(id=self.report_record.id)
        # assert the report archive does exist
        archived = ReportArchive.objects.get(rh_account=self.report_record.rh_account)
        archived_slice = ReportSliceArchive.objects.get(
            report_slice_id=self.report_slice.report_slice_id)
        self.assertEqual(str(archived.report_platform_id), str(self.uuid))
        self.assertEqual(str(archived_slice.report_platform_id), str(self.uuid))
        # assert the processor was reset
        self.check_variables_are_reset()

    def test_archive_report_and_slices_not_ready(self):
        """Test the archive method with slice not ready."""
        self.report_record.ready_to_archive = True
        self.report_record.report_platform_id = str(self.uuid)
        self.report_record.save()
        self.report_slice.ready_to_archive = False
        self.report_slice.report_platform_id = str(self.uuid)
        self.report_slice.report_slice_id = str(self.uuid2)
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice
        self.processor.report_platform_id = str(self.uuid)

        self.processor.archive_report_and_slices()
        # assert the report doesn't exist
        existing = Report.objects.get(id=self.report_record.id)
        # assert the report archive does exist
        with self.assertRaises(ReportArchive.DoesNotExist):
            ReportArchive.objects.get(rh_account=self.report_record.rh_account)
        with self.assertRaises(ReportSliceArchive.DoesNotExist):
            ReportSliceArchive.objects.get(
                report_slice_id=self.report_slice.report_slice_id)
        self.assertEqual(str(existing.report_platform_id), str(self.uuid))
        # assert the processor was reset
        self.check_variables_are_reset()
