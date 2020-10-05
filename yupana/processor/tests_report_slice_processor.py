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
from datetime import datetime, timedelta
from unittest.mock import patch

import pytz
from aiokafka import AIOKafkaProducer
from asynctest import CoroutineMock
from django.test import TestCase
from kafka.errors import ConnectionError as KafkaConnectionError
from processor import (abstract_processor,
                       report_consumer as msg_handler,
                       report_slice_processor,
                       tests_report_consumer as test_handler)
from prometheus_client import REGISTRY

from api.models import (Report,
                        ReportArchive,
                        ReportSlice,
                        ReportSliceArchive)
from config.settings.base import SATELLITE_HOST_TTL


# pylint: disable=too-many-public-methods
# pylint: disable=protected-access,too-many-lines,too-many-instance-attributes
class ReportSliceProcessorTests(TestCase):
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
        self.uuid8 = uuid.uuid4()
        self.fake_record = test_handler.KafkaMsg(msg_handler.QPC_TOPIC, 'http://internet.com')
        self.report_consumer = msg_handler.ReportConsumer()
        self.msg = self.report_consumer.unpack_consumer_record(self.fake_record)
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
            account='1234',
            state=Report.NEW,
            state_info=json.dumps([Report.NEW]),
            last_update_time=datetime.now(pytz.utc),
            retry_count=0,
            ready_to_archive=False,
            source='satellite',
            arrival_time=datetime.now(pytz.utc),
            processing_start_time=datetime.now(pytz.utc))
        self.report_record.save()

        self.report_slice = ReportSlice(
            report_platform_id=self.uuid,
            report_slice_id=self.uuid2,
            account='13423',
            report_json=json.dumps(self.report_json),
            state=ReportSlice.NEW,
            state_info=json.dumps([ReportSlice.NEW]),
            retry_count=0,
            last_update_time=datetime.now(pytz.utc),
            failed_hosts=[],
            candidate_hosts=[],
            report=self.report_record,
            ready_to_archive=True,
            hosts_count=2,
            source='satellite',
            creation_time=datetime.now(pytz.utc),
            processing_start_time=datetime.now(pytz.utc))
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

    def test_assign_report_slice_new(self):
        """Test the assign report slice function with only a new report slice."""
        self.report_slice.state = ReportSlice.NEW
        self.report_slice.save()
        self.processor.report_or_slice = None
        self.processor.assign_object()
        self.assertEqual(self.processor.report_or_slice, self.report_slice)
        queued_slices = REGISTRY.get_sample_value('queued_report_slices')
        self.assertEqual(queued_slices, 1)

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

        with patch('processor.report_slice_processor.'
                   'ReportSliceProcessor._validate_report_details',
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
        self.processor._upload_to_host_inventory_via_kafka = CoroutineMock(
            return_value=[])
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

    async def async_test_transition_to_hosts_uploaded_kafka_mode(self):
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
        self.processor._upload_to_host_inventory_via_kafka = CoroutineMock(
            return_value=[])
        await self.processor.transition_to_hosts_uploaded()
        self.assertEqual(json.loads(self.report_slice.candidate_hosts), [])
        self.assertEqual(self.report_slice.state, ReportSlice.HOSTS_UPLOADED)

    def test_transition_to_hosts_uploaded_kafka_mode(self):
        """Test the async hosts uploaded successful."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_transition_to_hosts_uploaded)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_test_transition_to_hosts_uploaded_no_candidates(self):
        """Test the transition to hosts being uploaded."""
        self.report_record.ready_to_archive = True
        self.report_record.save()
        faulty_report = ReportSlice(
            account='987',
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
                'ReportSliceProcessor._upload_to_host_inventory_via_kafka',
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

    async def async_test_upload_to_host_inventory_via_kafka(self):
        """Test uploading to inventory via kafka."""
        self.processor.report_or_slice = self.report_slice
        hosts = {
            str(self.uuid): {'bios_uuid': 'value', 'name': 'value'},
            str(self.uuid2): {'insights_client_id': 'value', 'name': 'foo'},
            str(self.uuid3): {'ip_addresses': 'value', 'name': 'foo'},
            str(self.uuid4): {'mac_addresses': 'value', 'name': 'foo'},
            str(self.uuid5): {'vm_uuid': 'value', 'name': 'foo'},
            str(self.uuid6): {'etc_machine_id': 'value'},
            str(self.uuid7): {'subscription_manager_id': 'value'},
            str(self.uuid8): {'system_profile': {'os_release': '7',
                                                 'os_kernel_version': '2.6.32'}
                              }}
        test_producer = AIOKafkaProducer(
            loop=report_slice_processor.SLICE_PROCESSING_LOOP,
            bootstrap_servers=report_slice_processor.INSIGHTS_KAFKA_ADDRESS
        )
        test_producer.start = CoroutineMock()
        test_producer.send = CoroutineMock()
        test_producer.stop = CoroutineMock()
        with patch('processor.report_slice_processor.AIOKafkaProducer',
                   return_value=test_producer):
            with patch('processor.report_slice_processor.asyncio.wait',
                       side_effect=None):
                # all though we are not asserting any results, the test here is
                # that no error was raised
                await self.processor._upload_to_host_inventory_via_kafka(hosts)

    def test_upload_to_host_inventory_via_kafka(self):
        """Test the async hosts uploaded exception."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_upload_to_host_inventory_via_kafka)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_test_upload_to_host_inventory_via_kafka_exception(self):
        """Test uploading to inventory via kafka."""
        self.processor.report_or_slice = self.report_slice
        hosts = {str(self.uuid): {'bios_uuid': 'value', 'name': 'value'},
                 str(self.uuid2): {'insights_client_id': 'value', 'name': 'foo'},
                 str(self.uuid3): {'ip_addresses': 'value', 'name': 'foo'},
                 str(self.uuid4): {'mac_addresses': 'value', 'name': 'foo'},
                 str(self.uuid5): {'vm_uuid': 'value', 'name': 'foo'},
                 str(self.uuid6): {'etc_machine_id': 'value'},
                 str(self.uuid7): {'subscription_manager_id': 'value'}}
        test_producer = AIOKafkaProducer(
            loop=report_slice_processor.SLICE_PROCESSING_LOOP,
            bootstrap_servers=report_slice_processor.INSIGHTS_KAFKA_ADDRESS
        )

        # test KafkaConnectionException
        def raise_kafka_error():
            """Raise a kafka error."""
            raise KafkaConnectionError('Test')

        test_producer.start = CoroutineMock(side_effect=raise_kafka_error)
        test_producer.send = CoroutineMock()
        test_producer.stop = CoroutineMock()
        with self.assertRaises(msg_handler.KafkaMsgHandlerError):
            with patch('processor.report_slice_processor.AIOKafkaProducer',
                       return_value=test_producer):
                await self.processor._upload_to_host_inventory_via_kafka(hosts)

    def test_upload_to_host_inventory_via_kafka_exception(self):
        """Test the async hosts uploaded exception."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(
            self.async_test_upload_to_host_inventory_via_kafka_exception)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_test_upload_to_host_inventory_via_kafka_send_exception(self):
        """Test uploading to inventory via kafka."""
        self.processor.report_or_slice = self.report_slice
        hosts = {str(self.uuid): {'bios_uuid': 'value', 'name': 'value'},
                 str(self.uuid2): {'insights_client_id': 'value', 'name': 'foo'},
                 str(self.uuid3): {'ip_addresses': 'value', 'name': 'foo'},
                 str(self.uuid4): {'mac_addresses': 'value', 'name': 'foo'},
                 str(self.uuid5): {'vm_uuid': 'value', 'name': 'foo'},
                 str(self.uuid6): {'etc_machine_id': 'value'},
                 str(self.uuid7): {'subscription_manager_id': 'value'}}
        test_producer = AIOKafkaProducer(
            loop=report_slice_processor.SLICE_PROCESSING_LOOP,
            bootstrap_servers=report_slice_processor.INSIGHTS_KAFKA_ADDRESS
        )

        # test KafkaConnectionException
        def raise_error():
            """Raise a general error."""
            raise Exception('Test')

        test_producer.start = CoroutineMock()
        test_producer.send = CoroutineMock(side_effect=raise_error)
        test_producer.stop = CoroutineMock()
        with self.assertRaises(msg_handler.KafkaMsgHandlerError):
            with patch('processor.report_slice_processor.AIOKafkaProducer',
                       return_value=test_producer):
                await self.processor._upload_to_host_inventory_via_kafka(hosts)

    def test_upload_to_host_inventory_via_kafka_send_exception(self):
        """Test the async hosts uploaded exception."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(
            self.async_test_upload_to_host_inventory_via_kafka_send_exception)
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
        archived = ReportArchive.objects.get(account=self.report_record.account)
        archived_slice = ReportSliceArchive.objects.get(
            report_slice_id=self.report_slice.report_slice_id)
        self.assertEqual(str(archived.report_platform_id), str(self.uuid))
        self.assertEqual(str(archived_slice.report_platform_id), str(self.uuid))
        self.assertIsNotNone(archived_slice.processing_end_time)
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
        archived = ReportArchive.objects.get(account=self.report_record.account)
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
            ReportArchive.objects.get(account=self.report_record.account)
        with self.assertRaises(ReportSliceArchive.DoesNotExist):
            ReportSliceArchive.objects.get(
                report_slice_id=self.report_slice.report_slice_id)
        self.assertEqual(str(existing.report_platform_id), str(self.uuid))
        # assert the processor was reset
        self.check_variables_are_reset()

    def test_get_stale_time(self):
        """Test the get stale date method."""
        self.processor.report_or_slice = self.report_record
        self.processor.report_or_slice.source = 'satellite'
        self.processor.report_or_slice.save()
        current_time = datetime.utcnow()
        stale_time = current_time + timedelta(days=int(SATELLITE_HOST_TTL))
        expected = stale_time.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        actual = self.processor.get_stale_date()
        # the format looks like this: 2019-11-14T19:58:13.037Z
        # by cutting off the last 13 i am comparing 2019-11-14T
        # which is the year/month/day
        self.assertEqual(expected[:-13], actual[:-13])

    def test_transform_os_release(self):
        """Test transform host os_release."""
        host = {'system_profile': {
            'os_release': 'Red Hat Enterprise Linux Server 6.10 (Santiago)'
        }}
        host = self.processor._transform_single_host(host)
        self.assertEqual(host, {'system_profile': {'os_release': '6.10'}})

    def test_do_not_transform_when_only_version(self):
        """Test do not transform os_release when only version."""
        host = {'system_profile': {'os_release': '7'}}
        host = self.processor._transform_single_host(host)
        self.assertEqual(host, {'system_profile': {'os_release': '7'}})

    def test_remove_os_release_when_no_version(self):
        """Test remove host os_release."""
        host = {
            'system_profile': {
                'os_release': 'Red Hat Enterprise Linux Server'}}
        host = self.processor._transform_single_host(host)
        self.assertEqual(host, {'system_profile': {}})

    def test_remove_os_release_when_no_version_with_parentheses(self):
        """Test remove host os_release when include empty parentheses."""
        host = {'system_profile': {'os_release': '  ()'}}
        host = self.processor._transform_single_host(host)
        self.assertEqual(host, {'system_profile': {}})

    def test_remove_os_release_when_only_string_in_parentheses(self):
        """Test remove host os_release when only string in parentheses."""
        host = {'system_profile': {'os_release': '  (Core)'}}
        host = self.processor._transform_single_host(host)
        self.assertEqual(host, {'system_profile': {}})

    def test_remove_os_release_when_empty_string(self):
        """Test remove host os_release when empty string."""
        host = {'system_profile': {'os_release': ''}}
        host = self.processor._transform_single_host(host)
        self.assertEqual(host, {'system_profile': {}})

    def test_transform_os_release_when_non_rhel_os(self):
        """Test transform host os_release when non rhel."""
        host = {'system_profile': {'os_release': 'CentOS Linux 7 (Core)'}}
        host = self.processor._transform_single_host(host)
        self.assertEqual(host, {'system_profile': {'os_release': '7'}})

    def test_transform_os_fields(self):
        """Test transform os fields."""
        host = {'system_profile': {
            'os_release': '7', 'os_kernel_version': '3.10.0-1127.el7.x86_64'
        }}
        host = self.processor._transform_single_host(host)
        self.assertEqual(
            host,
            {'system_profile': {
                'os_release': '7', 'os_kernel_version': '3.10.0'}})

    def test_do_not_tranform_os_fields(self):
        """Test do not transform os fields when already in format."""
        host = {'system_profile': {
            'os_release': '7', 'os_kernel_version': '2.6.32'}}
        host = self.processor._transform_single_host(host)
        self.assertEqual(
            host,
            {'system_profile': {
                'os_release': '7', 'os_kernel_version': '2.6.32'}}
        )
