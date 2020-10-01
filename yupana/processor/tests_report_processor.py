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
import io
import json
import tarfile
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytz
import requests
import requests_mock
from asynctest import CoroutineMock
from django.test import TransactionTestCase
from processor import (abstract_processor,
                       report_consumer as msg_handler,
                       report_processor,
                       tests_report_consumer as test_handler)
from prometheus_client import REGISTRY

from api.models import Report, ReportArchive, ReportSlice, Status


# pylint: disable=too-many-public-methods
# pylint: disable=protected-access,too-many-lines,too-many-instance-attributes
class ReportProcessorTests(TransactionTestCase):
    """Test Cases for the Message processor."""

    def setUp(self):
        """Create test setup."""
        self.payload_url = 'http://insights-upload.com/q/file_to_validate'
        self.uuid = uuid.uuid4()
        self.uuid2 = uuid.uuid4()
        self.uuid3 = uuid.uuid4()
        self.fake_record = test_handler.KafkaMsg(msg_handler.QPC_TOPIC, 'http://internet.com')
        self.report_consumer = msg_handler.ReportConsumer()
        self.msg = self.report_consumer.unpack_consumer_record(self.fake_record)
        self.report_json = {
            'report_id': 1,
            'report_slice_id': str(self.uuid2),
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
            source='qpc',
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
            creation_time=datetime.now(pytz.utc),
            processing_start_time=datetime.now(pytz.utc))
        self.report_slice.save()

        self.processor = report_processor.ReportProcessor()
        self.processor.report = self.report_record

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

    def test_archiving_report(self):
        """Test archiving creates archive, deletes current rep, and resets processor."""
        report_to_archive = Report(
            upload_srv_kafka_msg=json.dumps(self.msg),
            account='4321',
            report_platform_id=self.uuid2,
            state=Report.NEW,
            state_info=json.dumps([Report.NEW]),
            last_update_time=datetime.now(pytz.utc),
            retry_count=0,
            ready_to_archive=True,
            arrival_time=datetime.now(pytz.utc),
            processing_start_time=datetime.now(pytz.utc))
        report_to_archive.save()
        self.processor.report_or_slice = report_to_archive
        self.processor.account_number = '4321'
        self.processor.upload_message = self.msg
        self.processor.state = report_to_archive.state
        self.processor.report_platform_id = self.uuid
        self.processor.status = report_processor.SUCCESS_CONFIRM_STATUS

        self.processor.archive_report_and_slices()
        # assert the report doesn't exist
        with self.assertRaises(Report.DoesNotExist):
            Report.objects.get(id=report_to_archive.id)
        # assert the report archive does exist
        archived = ReportArchive.objects.get(account='4321')
        self.assertEqual(json.loads(archived.state_info), [Report.NEW])
        self.assertIsNotNone(archived.processing_end_time)
        # assert the processor was reset
        self.check_variables_are_reset()

    def test_archiving_report_not_ready(self):
        """Test that archiving fails if report not ready to archive."""
        report_to_archive = Report(
            upload_srv_kafka_msg=json.dumps(self.msg),
            account='4321',
            report_platform_id=self.uuid2,
            state=Report.NEW,
            state_info=json.dumps([Report.NEW]),
            last_update_time=datetime.now(pytz.utc),
            retry_count=0,
            ready_to_archive=False)
        report_to_archive.save()
        self.processor.report_or_slice = report_to_archive
        self.processor.account_number = '4321'
        self.processor.upload_message = self.msg
        self.processor.state = report_to_archive.state
        self.processor.report_platform_id = self.uuid
        self.processor.status = report_processor.SUCCESS_CONFIRM_STATUS

        self.processor.archive_report_and_slices()
        # assert the report still exist
        existing_report = Report.objects.get(id=report_to_archive.id)
        self.assertEqual(existing_report, report_to_archive)
        # assert the report archive does not exist
        with self.assertRaises(ReportArchive.DoesNotExist):
            ReportArchive.objects.get(account='4321')
        # assert the processor was reset
        self.check_variables_are_reset()

    def test_deduplicating_report(self):
        """Test that archiving creates archive rep, deletes report, and resets the processor."""
        self.report_record.report_platform_id = self.uuid
        self.report_record.save()
        report_to_dedup = Report(
            upload_srv_kafka_msg=json.dumps(self.msg),
            account='4321',
            report_platform_id=self.uuid,
            state=Report.NEW,
            upload_ack_status='success',
            state_info=json.dumps([Report.NEW]),
            last_update_time=datetime.now(pytz.utc),
            retry_count=0,
            ready_to_archive=True,
            arrival_time=datetime.now(pytz.utc),
            processing_start_time=datetime.now(pytz.utc))
        report_to_dedup.save()
        self.processor.report_or_slice = report_to_dedup
        self.processor.account_number = '4321'
        self.processor.upload_message = self.msg
        self.processor.state = report_to_dedup.state
        self.processor.report_platform_id = self.uuid
        self.processor.status = report_processor.SUCCESS_CONFIRM_STATUS

        self.processor.deduplicate_reports()
        # assert the report doesn't exist
        with self.assertRaises(Report.DoesNotExist):
            Report.objects.get(id=report_to_dedup.id)
        # assert the report archive does exist
        archived = ReportArchive.objects.get(account='4321')
        self.assertEqual(json.loads(archived.state_info), [Report.NEW])
        # assert the processor was reset
        self.check_variables_are_reset()

    def test_determine_retry_limit(self):
        """Test the determine retry method when the retry is at the limit."""
        self.report_record.state = Report.STARTED
        self.report_record.retry_count = 4
        self.report_record.save()
        self.processor.report_or_slice = self.report_record
        self.processor.determine_retry(Report.FAILED_DOWNLOAD,
                                       Report.STARTED)
        self.assertEqual(self.report_record.state, Report.FAILED_DOWNLOAD)
        self.assertEqual(self.report_record.ready_to_archive, True)

    def test_update_report_state(self):
        """Test updating the report state."""
        # set the base line values
        self.report_record.retry_count = 0
        self.report_record.save()
        self.processor.next_state = Report.STARTED
        # set the values we will update with
        self.processor.report_or_slice = self.report_record
        options = {'retry': abstract_processor.RETRY.increment,
                   'retry_type': Report.GIT_COMMIT,
                   'report_platform_id': self.uuid3}
        self.processor.update_object_state(options=options)
        self.assertEqual(self.report_record.retry_count, 1)
        self.assertEqual(self.report_record.retry_type, Report.GIT_COMMIT)
        self.assertEqual(self.report_record.report_platform_id, self.uuid3)

    async def async_test_run_method(self):
        """Test the run method."""
        self.report_record.state = Report.NEW
        self.report_record.save()
        self.processor.report_or_slice = None
        self.processor.should_run = True

        def transition_side_effect():
            self.processor.should_run = False

        with patch('processor.abstract_processor.'
                   'AbstractProcessor.transition_to_started',
                   side_effect=transition_side_effect):
            await self.processor.run()
            self.assertEqual(self.processor.report_or_slice, self.report_record)

    def test_run_method(self):
        """Test the async run function."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_run_method)
        event_loop.run_until_complete(coro())
        event_loop.close()

    def test_assign_report_new(self):
        """Test the assign report function with only a new report."""
        self.report_record.state = Report.NEW
        self.report_record.save()
        self.processor.report = None
        self.processor.assign_object()
        self.assertEqual(self.processor.report_or_slice, self.report_record)

    def test_assign_report_oldest_time(self):
        """Test the assign report function with older report."""
        current_time = datetime.now(pytz.utc)
        hours_old_time = current_time - timedelta(hours=9)
        older_report = Report(
            upload_srv_kafka_msg=json.dumps(self.msg),
            account='4321',
            report_platform_id=self.uuid2,
            state=Report.NEW,
            state_info=json.dumps([Report.NEW]),
            last_update_time=hours_old_time,
            retry_count=1)
        older_report.save()
        self.report_record.state = Report.NEW
        self.report_record.save()
        self.processor.report_or_slice = None
        self.processor.assign_object()
        self.assertEqual(self.processor.report_or_slice, older_report)
        # delete the older report object
        Report.objects.get(id=older_report.id).delete()

    def test_assign_report_not_old_enough(self):
        """Test the assign report function with young report."""
        # delete the report record
        Report.objects.get(id=self.report_record.id).delete()
        self.processor.report_or_slice = None
        current_time = datetime.now(pytz.utc)
        min_old_time = current_time - timedelta(minutes=1)
        older_report = Report(
            upload_srv_kafka_msg=json.dumps(self.msg),
            account='4321',
            report_platform_id=self.uuid2,
            state=Report.STARTED,
            state_info=json.dumps([Report.NEW]),
            last_update_time=min_old_time,
            retry_count=1)
        older_report.save()
        self.processor.assign_object()
        self.assertEqual(self.processor.report_or_slice, None)
        # delete the older report object
        Report.objects.get(id=older_report.id).delete()

    def test_assign_report_oldest_commit(self):
        """Test the assign report function with retry type as commit."""
        current_time = datetime.now(pytz.utc)
        twentyminold_time = current_time - timedelta(minutes=20)
        older_report = Report(
            upload_srv_kafka_msg=json.dumps(self.msg),
            account='4321',
            report_platform_id=self.uuid2,
            state=Report.DOWNLOADED,
            state_info=json.dumps([Report.NEW,
                                   Report.DOWNLOADED]),
            last_update_time=twentyminold_time,
            retry_count=1,
            retry_type=Report.GIT_COMMIT,
            git_commit='1234')
        older_report.save()
        self.report_record.state = Report.DOWNLOADED
        self.report_record.save()
        self.processor.report_or_slice = None
        # the commit should always be different from 1234
        self.processor.assign_object()
        self.assertEqual(self.processor.report_or_slice, older_report)
        self.assertEqual(self.processor.report_or_slice.state, Report.DOWNLOADED)
        # delete the older report object
        Report.objects.get(id=older_report.id).delete()

    def test_assign_report_no_reports(self):
        """Test the assign report method with no reports."""
        # delete the report record
        Report.objects.get(id=self.report_record.id).delete()
        self.processor.report_or_slice = None
        self.processor.assign_object()
        self.assertEqual(self.processor.report_or_slice, None)

    async def async_test_delegate_state(self):
        """Set up the test for delegate state."""
        self.report_record.state = Report.STARTED
        self.report_record.report_platform_id = self.uuid
        self.report_record.upload_ack_status = report_processor.SUCCESS_CONFIRM_STATUS
        self.report_record.save()
        self.processor.report_or_slice = self.report_record

        def download_side_effect():
            """Transition the state to downloaded."""
            self.processor.state = Report.DOWNLOADED
            self.report_record.state = Report.DOWNLOADED
            self.report_record.save()
        with patch('processor.report_processor.'
                   'ReportProcessor.transition_to_downloaded',
                   side_effect=download_side_effect):
            await self.processor.delegate_state()
            self.assertEqual(self.processor.report_platform_id,
                             self.report_record.report_platform_id)
            # self.assertEqual(self.processor.report_or_slice.state, Report.DOWNLOADED)
            self.assertEqual(self.processor.status, self.processor.report.upload_ack_status)

        # test the async function call state
        self.report_record.state = Report.VALIDATED
        self.report_record.save()

        def validation_reported_side_effect():
            """Side effect for async transition method."""
            self.report_record.state = Report.VALIDATION_REPORTED
            self.report_record.save()
        self.processor.transition_to_validation_reported = CoroutineMock(
            side_effect=validation_reported_side_effect)
        await self.processor.delegate_state()

    def test_run_delegate(self):
        """Test the async function delegate state."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_delegate_state)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_test_delegate_state_exception(self):
        """Set up the test for delegate state with exception."""
        self.report_record.state = Report.STARTED
        self.report_record.report_platform_id = self.uuid
        self.report_record.upload_ack_status = report_processor.SUCCESS_CONFIRM_STATUS
        self.report_record.save()
        self.processor.report_or_slice = self.report_record

        def delegate_side_effect():
            """Transition the state to downloaded."""
            self.processor.should_run = False
            raise Exception('Test')
        with patch('processor.report_processor.ReportProcessor.delegate_state',
                   side_effect=delegate_side_effect):
            await self.processor.run()
            self.check_variables_are_reset()

    def test_run_delegate_exception(self):
        """Test the async function delegate state."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_delegate_state_exception)
        event_loop.run_until_complete(coro())
        event_loop.close()

    def test_reinit_variables(self):
        """Test that reinitting the variables clears the values."""
        # make sure that the variables have values
        self.processor.report_platform_id = self.uuid
        self.processor.report_or_slice = self.report_record
        self.processor.state = Report.NEW
        self.processor.account_number = '1234'
        self.processor.upload_message = self.msg
        self.processor.report_json = {}
        self.processor.candidate_hosts = []
        self.processor.failed_hosts = []
        self.processor.status = report_processor.SUCCESS_CONFIRM_STATUS
        self.assertEqual(self.processor.report_or_slice, self.report_record)
        self.assertEqual(self.processor.report_platform_id, self.uuid)
        self.assertEqual(self.processor.state, Report.NEW)
        self.assertEqual(self.processor.account_number, '1234')
        self.assertEqual(self.processor.upload_message, self.msg)
        self.assertEqual(self.processor.status, report_processor.SUCCESS_CONFIRM_STATUS)

        # check all of the variables are None after reinitting
        self.processor.reset_variables()
        self.check_variables_are_reset()

    def test_transition_to_started(self):
        """Test the transition to started state."""
        self.report_record.state = Report.NEW
        self.processor.report_or_slice = self.report_record
        self.processor.transition_to_started()
        self.assertEqual(self.report_record.state, Report.STARTED)
        self.assertEqual(json.loads(self.report_record.state_info),
                         [Report.NEW, Report.STARTED])

    def test_transition_to_downloaded(self):
        """Test that the transition to download works successfully."""
        metadata_json = {
            'report_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'source': 'qpc',
            'host_inventory_api_version': '1.0.0',
            'report_slices': {str(self.uuid): {'number_hosts': 1}}
        }
        report_json = {
            'report_slice_id': str(self.uuid),
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': [{'key': 'value'}]}

        report_files = {
            '%s.json' % str(self.uuid): report_json,
            'metadata.json': metadata_json
        }
        self.processor.upload_message = {'url': self.payload_url, 'rh_account': '00001'}
        self.processor.report_or_slice = self.report_record
        self.processor.account_number = '0001'
        buffer_content = test_handler.create_tar_buffer(report_files)
        with requests_mock.mock() as mock_req:
            mock_req.get(self.payload_url, content=buffer_content)
            self.processor.transition_to_downloaded()
            self.assertEqual(self.report_record.state, Report.DOWNLOADED)

    def test_transition_to_downloaded_satellite(self):
        """Test that the transition to download works successfully wit sat source."""
        metadata_json = {
            'report_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'source': 'satellite',
            'source_metadata': {'foo': 'bar'},
            'host_inventory_api_version': '1.0.0',
            'report_slices': {str(self.uuid): {'number_hosts': 1}}
        }
        report_json = {
            'report_slice_id': str(self.uuid),
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': [{'key': 'value'}]}

        report_files = {
            '%s.json' % str(self.uuid): report_json,
            'metadata.json': metadata_json
        }
        self.processor.upload_message = {'url': self.payload_url, 'rh_account': '00001'}
        self.processor.report_or_slice = self.report_record
        self.processor.account_number = '0001'
        buffer_content = test_handler.create_tar_buffer(report_files)
        with requests_mock.mock() as mock_req:
            mock_req.get(self.payload_url, content=buffer_content)
            self.processor.transition_to_downloaded()
            self.assertEqual(self.report_record.state, Report.DOWNLOADED)

    def test_transition_to_downloaded_exception_retry(self):
        """Test that the transition to download with retry exception."""
        self.processor.upload_message = {'url': self.payload_url, 'rh_account': '00001'}
        self.report_record.state = Report.STARTED
        self.report_record.save()
        self.processor.report_or_slice = self.report_record
        with requests_mock.mock() as mock_req:
            mock_req.get(self.payload_url, exc=requests.exceptions.HTTPError)
            self.processor.transition_to_downloaded()
            self.assertEqual(self.report_record.state, Report.STARTED)
            self.assertEqual(self.report_record.retry_count, 1)

    def test_transition_to_downloaded_exception_fail(self):
        """Test that the transition to download with fail exception."""
        self.processor.upload_message = {'url': self.payload_url, 'rh_account': '00001'}
        self.report_record.state = Report.STARTED
        self.report_record.save()
        self.processor.report_or_slice = self.report_record

        def download_side_effect():
            """Raise a FailDownloadException."""
            raise report_processor.FailDownloadException()

        with patch('processor.report_processor.ReportProcessor._download_report',
                   side_effect=download_side_effect):
            self.processor.transition_to_downloaded()
            self.assertEqual(self.report_record.state, Report.FAILED_DOWNLOAD)

    def test_transition_to_validated_report_exception(self):
        """Test that a report with no report_slice_id is still marked as validated."""
        self.report_record.state = Report.DOWNLOADED
        self.report_record.save()
        report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {str(self.uuid): {'key': 'value'}}}
        self.report_slice.state = ReportSlice.PENDING
        self.report_slice.report_json = json.dumps(report_json)
        self.report_slice.save()
        self.processor.report_or_slice = self.report_record
        self.processor.transition_to_validated()
        self.assertEqual(self.report_record.state, Report.VALIDATED)
        self.assertEqual(self.report_record.upload_ack_status,
                         report_processor.FAILURE_CONFIRM_STATUS)
        self.assertEqual(self.report_record.retry_count, 0)

    def test_transition_to_validated_general_exception(self):
        """Test that exceptions are validated as failure (if no slices are valid)."""
        self.report_record.state = Report.DOWNLOADED
        self.report_record.save()
        self.processor.report_or_slice = self.report_record

        def validate_side_effect():
            """Transition the state to downloaded."""
            raise Exception('Test')

        with patch('processor.report_processor.'
                   'ReportProcessor._validate_report_details',
                   side_effect=validate_side_effect):
            self.processor.transition_to_validated()
            self.assertEqual(self.report_record.state, Report.VALIDATED)
            self.assertEqual(self.processor.status, report_processor.FAILURE_CONFIRM_STATUS)

    async def async_test_transition_to_validation_reported(self):
        """Set up the test for transitioning to validation reported."""
        self.report_record.state = Report.VALIDATED
        self.report_record.report_platform_id = self.uuid
        self.report_record.upload_ack_status = report_processor.SUCCESS_CONFIRM_STATUS
        self.report_record.save()
        self.processor.report_or_slice = self.report_record
        self.processor.status = report_processor.SUCCESS_CONFIRM_STATUS
        self.processor.upload_message = {'request_id': self.uuid}

        self.processor._send_confirmation = CoroutineMock()
        await self.processor.transition_to_validation_reported()
        self.assertEqual(self.processor.report.state, Report.VALIDATION_REPORTED)

    def test_transition_to_validation_reported(self):
        """Test the async function to transition to validation reported."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_transition_to_validation_reported)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_test_transition_to_validation_reported_exception(self):
        """Set up the test for transitioning to validation reported."""
        self.report_record.state = Report.VALIDATED
        self.report_record.retry_count = 0
        self.report_record.report_platform_id = self.uuid
        self.report_record.upload_ack_status = report_processor.SUCCESS_CONFIRM_STATUS
        self.report_record.save()
        self.processor.report_or_slice = self.report_record
        self.processor.status = report_processor.SUCCESS_CONFIRM_STATUS
        self.processor.upload_message = {'hash': self.uuid}

        def report_side_effect():
            """Transition the state to validation_reported."""
            raise Exception('Test')

        self.processor._send_confirmation = CoroutineMock(side_effect=report_side_effect)
        await self.processor.transition_to_validation_reported()
        self.assertEqual(self.report_record.state, Report.VALIDATED)
        self.assertEqual(self.report_record.retry_count, 1)
        self.check_variables_are_reset()

    def test_transition_to_validation_reported_exception(self):
        """Test the async function to transition to validation reported."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(
            self.async_test_transition_to_validation_reported_exception)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_transition_to_validation_reported_failure_status(self):
        """Set up the test for transitioning to validation reported failure status."""
        report_to_archive = Report(
            upload_srv_kafka_msg=json.dumps(self.msg),
            account='43214',
            report_platform_id=self.uuid2,
            state=Report.VALIDATED,
            state_info=json.dumps([Report.NEW]),
            last_update_time=datetime.now(pytz.utc),
            retry_count=0,
            retry_type=Report.TIME,
            ready_to_archive=True,
            arrival_time=datetime.now(pytz.utc),
            processing_start_time=datetime.now(pytz.utc))
        report_to_archive.upload_ack_status = report_processor.FAILURE_CONFIRM_STATUS
        report_to_archive.save()
        self.processor.report_or_slice = report_to_archive
        self.processor.report_platform_id = self.uuid2
        self.processor.account_number = '43214'
        self.processor.state = Report.VALIDATED
        self.processor.status = report_processor.FAILURE_CONFIRM_STATUS
        self.processor.upload_message = {'request_id': self.uuid}
        self.processor._send_confirmation = CoroutineMock()
        await self.processor.transition_to_validation_reported()
        with self.assertRaises(Report.DoesNotExist):
            Report.objects.get(id=report_to_archive.id)
        archived = ReportArchive.objects.get(account='43214')
        self.assertEqual(archived.state,
                         Report.VALIDATION_REPORTED)
        self.assertEqual(archived.upload_ack_status,
                         report_processor.FAILURE_CONFIRM_STATUS)
        # assert the processor was reset
        self.check_variables_are_reset()

    def test_transition_to_validation_reported_failure(self):
        """Test the async function for reporting failure status."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_transition_to_validation_reported_failure_status)
        event_loop.run_until_complete(coro())
        event_loop.close()

    # Tests for the functions that carry out the work ie (download/upload)
    def test_validate_report_success(self):
        """Test that a QPC report with the correct structure passes validation."""
        self.processor.account_number = '123'
        self.processor.report_or_slice = self.report_record
        self.processor.report_json = {
            'report_id': 1,
            'report_slice_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319-1',
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': [{'bios_uuid': 'value', 'facts': []}]}
        valid = self.processor._validate_report_details()
        for _, value in valid[0].items():
            self.assertIn('bios_uuid', value)
            self.assertIn('account', value)
            self.assertIn('facts', value)
            self.assertEqual(value['bios_uuid'], 'value')
            self.assertEqual(value['account'], '123')

    def test_validate_report_missing_id(self):
        """Test that a QPC report with a missing id is fails validation."""
        self.processor.report_json = {
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'report_slice_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319-1',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'key': 'value'}}}
        self.processor.report_or_slice = self.report_record

        with self.assertRaises(msg_handler.QPCReportException):
            self.processor._validate_report_details()

    def test_validate_report_fails_no_canonical_facts(self):
        """Test to verify a QPC report with the correct structure passes validation."""
        self.processor.report_json = {
            'report_id': 1,
            'report_slice_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319-1',
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'name': 'value'}}}
        self.processor.report_or_slice = self.report_record

        with self.assertRaises(msg_handler.QPCReportException):
            self.processor._validate_report_details()

    def test_validate_report_invalid_report_type(self):
        """Test to verify a QPC report with an invalid report_type is failed."""
        self.processor.report_json = {
            'report_id': 1,
            'report_slice_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319-1',
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'key': 'value'}}}
        self.processor.report_or_slice = self.report_record

        with self.assertRaises(msg_handler.QPCReportException):
            self.processor._validate_report_details()

    def test_validate_report_missing_version(self):
        """Test to verify a QPC report missing report_version is failed."""
        self.processor.report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_slice_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319-1',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'key': 'value'}}}
        self.processor.report_or_slice = self.report_record

        with self.assertRaises(msg_handler.QPCReportException):
            self.processor._validate_report_details()

    def test_validate_report_missing_platform_id(self):
        """Test to verify a QPC report missing report_platform_id is failed."""
        self.processor.report_json = {
            'report_id': 1,
            'report_slice_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319-1',
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'hosts': {self.uuid: {'key': 'value'}}}
        self.processor.report_or_slice = self.report_record

        with self.assertRaises(msg_handler.QPCReportException):
            self.processor._validate_report_details()

    def test_validate_report_missing_hosts(self):
        """Test to verify a QPC report with empty hosts is failed."""
        self.processor.report_json = {
            'report_id': 1,
            'report_slice_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319-1',
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {}}
        self.processor.report_or_slice = self.report_record

        with self.assertRaises(msg_handler.QPCReportException):
            self.processor._validate_report_details()

    def test_validate_report_missing_slice_id(self):
        """Test to verify a QPC report with no report_slice_id is failed."""
        self.processor.report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'key': 'value'}}}
        self.processor.report_or_slice = self.report_record

        with self.assertRaises(msg_handler.QPCReportException):
            self.processor._validate_report_details()

    def test_validate_report_invalid_hosts(self):
        """Test to verify a QPC report with invalid hosts is failed."""
        self.processor.report_json = {
            'report_id': 1,
            'report_slice_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319-1',
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': ['foo']}
        self.processor.report_or_slice = self.report_record

        with self.assertRaises(msg_handler.QPCReportException):
            self.processor._validate_report_details()

    def test_validate_report_invalid_hosts_key(self):
        """Test to verify a QPC report with invalid hosts key is failed."""
        self.processor.report_json = {
            'report_id': 1,
            'report_slice_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319-1',
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {1: {'foo': 'bar'}}}
        self.processor.report_or_slice = self.report_record

        with self.assertRaises(msg_handler.QPCReportException):
            self.processor._validate_report_details()

    def test_validate_report_invalid_hosts_val(self):
        """Test to verify a QPC report with invalid hosts value is failed."""
        self.processor.report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_slice_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319-1',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: ['foo']}}
        self.processor.report_or_slice = self.report_record

        with self.assertRaises(msg_handler.QPCReportException):
            self.processor._validate_report_details()

    def test_validate_report_hosts(self):
        """Test host verification."""
        # test all valid hosts
        self.processor.report_platform_id = self.uuid2
        self.processor.account_number = '12345'
        self.processor.report_or_slice = self.report_record
        hosts = [{'bios_uuid': 'value', 'name': 'value', 'facts': []},
                 {'insights_client_id': 'value', 'name': 'foo', 'facts': []},
                 {'ip_addresses': 'value', 'name': 'foo', 'facts': []},
                 {'mac_addresses': 'value', 'name': 'foo', 'facts': []},
                 {'vm_uuid': 'value', 'name': 'foo', 'facts': []},
                 {'etc_machine_id': 'value', 'facts': []},
                 {'subscription_manager_id': 'value', 'facts': []},
                 {'not_valid': 'value', 'facts': []}
                 ]

        self.processor.report_json = {
            'report_id': 1,
            'report_slice_id': self.uuid,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': hosts}
        source_metadata = {'server_version': '0.0.0'}
        actual_valid, actual_invalid = self.processor._validate_report_hosts(
            self.uuid, source_metadata)
        for valid_host in actual_valid:
            for host_id, host in valid_host.items():
                self.assertIn('facts', host)
                self.assertEqual(host['facts'],
                                 [{'namespace': 'yupana',
                                   'facts': {'yupana_host_id': host_id,
                                             'report_platform_id': str(self.uuid2),
                                             'report_slice_id': str(self.uuid),
                                             'account': '12345',
                                             'source': 'qpc'}}])
        for invalid_host in actual_invalid:
            for host_id, host in invalid_host.items():
                if host_id != 'cause':
                    self.assertIn('facts', host)
                    self.assertIn('not_valid', host)
                    self.assertEqual(host['facts'],
                                     [{'namespace': 'yupana',
                                       'facts': {'yupana_host_id': host_id,
                                                 'report_platform_id': str(self.uuid2),
                                                 'report_slice_id': str(self.uuid),
                                                 'account': '12345',
                                                 'source': 'qpc'}}])

        # test that invalid hosts are not removed
        invalid_host = {'no': 'canonical facts', 'metadata': []}
        hosts.append(invalid_host)
        source_metadata = {'version': '0.0.0.1'}
        valid_hosts, _ = self.processor._validate_report_hosts(
            self.uuid, source_metadata)
        kept_invalid = False
        for valid_host in valid_hosts:
            for host_id, host in valid_host.items():
                if 'no' in host:
                    kept_invalid = True
        self.assertEqual(kept_invalid, True)

    def test_update_slice_exception(self):
        """Test udpating the slice with invalid data."""
        # test that not providing a state inside options causes
        # an exception to be raised and slice is not updated
        self.report_slice.state = ReportSlice.PENDING
        self.report_slice.save()
        self.processor.update_slice_state({}, self.report_slice)
        self.report_slice.refresh_from_db()
        self.assertEqual(self.report_slice.state, ReportSlice.PENDING)

    def test_extract_and_create_slices_success(self):
        """Testing the extract method with valid buffer content."""
        metadata_json = {
            'report_id': 1,
            'host_inventory_api_version': '1.0.0',
            'source': 'qpc',
            'source_metadata': {'foo': 'bar'},
            'report_slices': {str(self.uuid): {'number_hosts': 1}}
        }
        report_json = {
            'report_slice_id': str(self.uuid),
            'hosts': {str(self.uuid): {'key': 'value'}}}
        report_files = {
            'metadata.json': metadata_json,
            '%s.json' % str(self.uuid): report_json
        }
        self.processor.report_or_slice = self.report_record
        self.processor.account_number = '0001'
        buffer_content = test_handler.create_tar_buffer(report_files)
        result = self.processor._extract_and_create_slices(buffer_content)
        expected_result = {
            'report_platform_id': 1,
            'host_inventory_api_version': '1.0.0',
            'source': 'qpc',
            'source_metadata': {'foo': 'bar'}
        }
        self.assertEqual(result, expected_result)

    def test_extract_and_create_slices_mismatch(self):
        """Testing the extract method with mismatched metadata content."""
        metadata_json = {
            'report_id': 1,
            'host_inventory_api_version': '1.0.0',
            'source': 'qpc',
            'source_metadata': {'foo': 'bar'},
            'report_slices': {str(self.uuid): {'number_hosts': 5}}
        }
        report_json = {
            'report_slice_id': '1234556',
            'hosts': {str(self.uuid): {'key': 'value'}}}
        report_files = {
            'metadata.json': metadata_json,
            '%s.json' % str(self.uuid): report_json
        }
        self.processor.report_or_slice = self.report_record
        self.processor.account_number = '0001'
        buffer_content = test_handler.create_tar_buffer(report_files)
        with self.assertRaises(report_processor.FailExtractException):
            self.processor._extract_and_create_slices(buffer_content)

    def test_extract_and_create_slices_too_many_hosts(self):
        """Testing the extract method with valid buffer content."""
        metadata_json = {
            'report_id': 1,
            'host_inventory_api_version': '1.0.0',
            'source': 'qpc',
            'source_metadata': {'foo': 'bar'},
            'report_slices': {str(self.uuid): {'number_hosts': 1000000}}
        }
        report_json = {
            'report_slice_id': str(self.uuid),
            'hosts': {str(self.uuid): {'key': 'value'}}}
        report_files = {
            'metadata.json': metadata_json,
            '%s.json' % str(self.uuid): report_json
        }
        self.processor.report_or_slice = self.report_record
        self.processor.account_number = '0001'
        buffer_content = test_handler.create_tar_buffer(report_files)
        with self.assertRaises(report_processor.FailExtractException):
            self.processor._extract_and_create_slices(buffer_content)

    def test_extract_and_create_slices_metadata_fail(self):
        """Testing the extract method with invalid metadata buffer content."""
        metadata_json = 'myfakeencodedstring'
        report_json = {
            'report_slice_id': str(self.uuid),
            'hosts': {str(self.uuid): {'key': 'value'}}}
        report_files = {
            'metadata.json': metadata_json,
            '%s.json' % str(self.uuid): report_json
        }
        self.processor.report_or_slice = self.report_record
        self.processor.account_number = '0001'
        buffer_content = test_handler.create_tar_buffer(report_files, meta_encoding='utf-16')
        with self.assertRaises(report_processor.FailExtractException):
            self.processor._extract_and_create_slices(buffer_content)

    def test_extract_and_create_slices_slice_fail(self):
        """Testing the extract method with bad slice."""
        metadata_json = {
            'report_id': 1,
            'host_inventory_api_version': '1.0.0',
            'source': 'qpc',
            'source_metadata': {'foo': 'bar'},
            'report_slices': {str(self.uuid): {'number_hosts': 1}}
        }
        report_json = 'myfakeencodedstring'
        report_files = {
            'metadata.json': metadata_json,
            '%s.json' % str(self.uuid): report_json
        }
        self.processor.report_or_slice = self.report_record
        self.processor.account_number = '0001'
        buffer_content = test_handler.create_tar_buffer(report_files,
                                                        encoding='utf-16',
                                                        meta_encoding='utf-8')
        with self.assertRaises(report_processor.FailExtractException):
            self.processor._extract_and_create_slices(buffer_content)

    def test_create_slice_invalid(self):
        """Test the create slice method with an invalid slice."""
        report_json = None
        slice_id = '1234556'
        hosts_count = 1
        with self.assertRaises(Exception):
            options = {
                'report_json': report_json,
                'report_slice_id': slice_id,
                'hosts_count': hosts_count,
                'source': 'qpc',
            }
            self.processor.create_report_slice(options)

    def test_extract_and_create_slices_two_reps_copy(self):
        """Testing the extract method with valid buffer content."""
        self.report_slice.report = self.report_record
        self.report_slice.report_platform_id = self.uuid
        self.report_slice.report_slice_id = self.uuid2
        self.report_slice.save()
        self.processor.report_platform_id = self.uuid
        metadata_json = {
            'source': 'qpc',
            'report_id': str(self.uuid),
            'host_inventory_api_version': '1.0.0',
            'report_slices': {str(self.uuid): {'number_hosts': 1},
                              str(self.uuid2): {'number_hosts': 1}}
        }
        report_json = {
            'report_slice_id': str(self.uuid),
            'hosts': {str(self.uuid): {'key': 'value'}}}
        report_json2 = {
            'report_slice_id': str(self.uuid2),
            'hosts': {str(self.uuid): {'key': 'value'}}}
        report_files = {
            'metadata.json': metadata_json,
            '%s.json' % str(self.uuid): report_json,
            '%s.json' % str(self.uuid2): report_json2
        }
        self.processor.report_or_slice = self.report_record
        self.processor.account_number = '000001'
        buffer_content = test_handler.create_tar_buffer(report_files)
        result = self.processor._extract_and_create_slices(buffer_content)
        expected_result = {
            'report_platform_id': str(self.uuid),
            'host_inventory_api_version': '1.0.0',
            'source': 'qpc',
        }
        self.assertEqual(result, expected_result)

    def test_extract_and_create_slices_two_reps(self):
        """Testing the extract method with valid buffer content."""
        metadata_json = {
            'source': 'qpc',
            'host_inventory_api_version': '1.0.0',
            'report_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'report_slices': {str(self.uuid): {'number_hosts': 1}}
        }
        report_json = {
            'report_slice_id': str(self.uuid),
            'hosts': [{'key': 'value'}]}

        report_files = {
            'metadata.json': metadata_json,
            '%s.json' % str(self.uuid): report_json
        }
        self.processor.report_or_slice = self.report_record
        self.processor.account_number = '0001'
        buffer_content = test_handler.create_tar_buffer(report_files)
        result = self.processor._extract_and_create_slices(buffer_content)
        expected_result = {
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'host_inventory_api_version': '1.0.0',
            'source': 'qpc',
        }
        self.assertEqual(result, expected_result)

    def test_extract_and_create_slices_failure(self):
        """Testing the extract method failure no matching report_slice."""
        metadata_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'report_version': '1.0.0.1b025b8',
            'report_slices': {str(self.uuid): {'number_hosts': 1}}
        }
        report_files = {
            'metadata.json': metadata_json
        }
        buffer_content = test_handler.create_tar_buffer(report_files)
        with self.assertRaises(report_processor.FailExtractException):
            self.processor._extract_and_create_slices(buffer_content)

    def test_extract_and_create_slices_failure_invalid_metadata(self):
        """Testing the extract method failure no valid metadata."""
        metadata_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'report_version': '1.0.0.1b025b8',
            'report_slices': {str(self.uuid): {'number_hosts': 1}}
        }
        report_json = {
            'report_slice_id': str(self.uuid),
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {str(self.uuid): {'key': 'value'}}}
        report_files = {
            'metadata.json': metadata_json,
            '%s.json' % str(self.uuid): report_json
        }
        buffer_content = test_handler.create_tar_buffer(report_files)
        with self.assertRaises(report_processor.FailExtractException):
            self.processor._extract_and_create_slices(buffer_content)

    def test_extract_and_create_slices_failure_no_metadata(self):
        """Testing the extract method failure no json file."""
        report_json = {
            'report_slice_id': '2345322',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {str(self.uuid): {'key': 'value'}}}
        report_files = {
            '2345322.json': report_json
        }
        buffer_content = test_handler.create_tar_buffer(report_files)
        with self.assertRaises(report_processor.FailExtractException):
            self.processor._extract_and_create_slices(buffer_content)

    def test__extract_and_create_slices_failure_invalid_json(self):
        """Testing the extract method failure invalid json."""
        metadata_json = {
            'report_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'host_inventory_api_version': '1.0',
            'source': 'qpc',
            'report_slices': {'2345322': {'number_hosts': 1}}
        }
        report_json = 'This is not JSON.'
        report_files = {
            '2345322.json': report_json,
            'metadata.json': metadata_json
        }
        buffer_content = test_handler.create_tar_buffer(report_files)
        with self.assertRaises(report_processor.RetryExtractException):
            self.processor._extract_and_create_slices(buffer_content)

    def test__extract_and_create_slices_failure_no_json(self):
        """Testing the extract method failure invalid json."""
        metadata_json = {
            'report_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'source': 'qpc',
            'host_inventory_api_version': '1.0.0.',
            'report_slices': {'2345322': {'number_hosts': 1}}
        }
        report_json = None
        report_files = {
            '2345322.json': report_json,
            'metadata.json': metadata_json
        }
        buffer_content = test_handler.create_tar_buffer(report_files)
        with self.assertRaises(report_processor.FailExtractException):
            self.processor._extract_and_create_slices(buffer_content)

    def test_download_response_content_bad_url(self):
        """Test to verify download exceptions are handled."""
        with requests_mock.mock() as mock_req:
            mock_req.get(self.payload_url, exc=requests.exceptions.HTTPError)
            with self.assertRaises(report_processor.RetryDownloadException):
                self.processor.upload_message = {'url': self.payload_url}
                self.processor._download_report()

    def test_download_response_content_missing_url(self):
        """Test case where url is missing."""
        with self.assertRaises(report_processor.FailDownloadException):
            self.processor.upload_message = {}
            self.processor._download_report()

    def test_download_report_success(self):
        """Test to verify extracting contents is successful."""
        metadata_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'report_version': '1.0.0.1b025b8',
            'report_slices': {'2345322': {'number_hosts': 1}}
        }
        report_json = {
            'report_slice_id': '2345322',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {str(self.uuid): {'key': 'value'}}}
        report_files = {
            'metadata.json': metadata_json,
            '2345322.json': report_json
        }
        self.processor.upload_message = {'url': self.payload_url, 'rh_account': '00001'}
        buffer_content = test_handler.create_tar_buffer(report_files)
        with requests_mock.mock() as mock_req:
            mock_req.get(self.payload_url, content=buffer_content)
            content = self.processor._download_report()
            self.assertEqual(buffer_content, content)

    def test_download_and_validate_contents_invalid_report(self):
        """Test to verify extracting contents fails when report is invalid."""
        self.processor.report_json = {
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'key': 'value'}}}
        self.processor.report_or_slice = self.report_record
        with self.assertRaises(msg_handler.QPCReportException):
            _, _ = self.processor._validate_report_details()

    def test_download_contents_raises_error(self):
        """Test to verify downloading contents fails when error is raised."""
        report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {str(self.uuid): {'key': 'value'}}}
        self.processor.upload_message = {'url': self.payload_url, 'rh_account': '00001'}
        report_files = {'report.json': report_json}
        buffer_content = test_handler.create_tar_buffer(report_files)
        with requests_mock.mock() as mock_req:
            mock_req.get(self.payload_url, content=buffer_content)
            with patch('requests.get', side_effect=requests.exceptions.HTTPError):
                with self.assertRaises(report_processor.RetryDownloadException):
                    content = self.processor._download_report()
                    self.assertEqual(content, buffer_content)

    def test_download_with_404(self):
        """Test downloading a URL and getting 404."""
        with requests_mock.mock() as mock_req:
            mock_req.get(self.payload_url, status_code=404)
            with self.assertRaises(report_processor.RetryDownloadException):
                self.processor.upload_message = {'url': self.payload_url}
                self.processor._download_report()

    def test_value_error__extract_and_create_slices(self):
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
        with self.assertRaises(report_processor.FailExtractException):
            self.processor._extract_and_create_slices(buffer_content)

    def test_no_json_files__extract_and_create_slices(self):
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
        with self.assertRaises(report_processor.FailExtractException):
            self.processor._extract_and_create_slices(buffer_content)

    def test__extract_and_create_slices_general_except(self):
        """Testing general exception raises retry exception."""
        def extract_side_effect():
            """Raise general error."""
            raise Exception('Test')

        with patch('processor.report_processor.tarfile.open',
                   side_effect=extract_side_effect):
            with self.assertRaises(report_processor.RetryExtractException):
                self.processor._extract_and_create_slices(None)

    def test_calculating_queued_reports(self):
        """Test the calculate_queued_reports method."""
        status_info = Status()
        current_time = datetime.now(pytz.utc)
        self.report_record.state = Report.NEW
        self.report_record.save()
        reports_to_process = self.processor.calculate_queued_objects(current_time, status_info)
        self.assertEqual(reports_to_process, 1)

        min_old_time = current_time - timedelta(hours=8)
        older_report = Report(
            upload_srv_kafka_msg=json.dumps(self.msg),
            account='4321',
            report_platform_id=self.uuid2,
            state=Report.STARTED,
            state_info=json.dumps([Report.NEW]),
            last_update_time=min_old_time,
            retry_count=1,
            retry_type=Report.TIME)
        older_report.save()

        retry_commit_report = Report(
            upload_srv_kafka_msg=json.dumps(self.msg),
            account='4321',
            report_platform_id=self.uuid2,
            state=Report.DOWNLOADED,
            state_info=json.dumps([Report.NEW]),
            last_update_time=min_old_time,
            git_commit='3948384729',
            retry_type=Report.GIT_COMMIT,
            retry_count=1)
        retry_commit_report.save()

        # create some reports that should not be counted
        not_old_enough = current_time - timedelta(hours=1)
        too_young_report = Report(
            upload_srv_kafka_msg=json.dumps(self.msg),
            account='4321',
            report_platform_id=self.uuid2,
            state=Report.DOWNLOADED,
            state_info=json.dumps([Report.NEW]),
            last_update_time=not_old_enough,
            git_commit='3948384729',
            retry_type=Report.TIME,
            retry_count=1)
        too_young_report.save()

        same_commit_report = Report(
            upload_srv_kafka_msg=json.dumps(self.msg),
            account='4321',
            report_platform_id=self.uuid2,
            state=Report.DOWNLOADED,
            state_info=json.dumps([Report.NEW]),
            last_update_time=min_old_time,
            git_commit=status_info.git_commit,
            retry_type=Report.GIT_COMMIT,
            retry_count=1)
        same_commit_report.save()

        reports_to_process = self.processor.calculate_queued_objects(current_time, status_info)
        self.assertEqual(reports_to_process, 3)

        # delete the older report object
        Report.objects.get(id=older_report.id).delete()
        Report.objects.get(id=retry_commit_report.id).delete()
        Report.objects.get(id=too_young_report.id).delete()
        Report.objects.get(id=same_commit_report.id).delete()

    def test_state_to_metric(self):
        """Test the state_to_metric function."""
        self.processor.state = Report.FAILED_DOWNLOAD
        self.processor.account_number = '1234'
        failed_download_before = \
            REGISTRY.get_sample_value(
                'failed_download_total', {'account_number': '1234'}) or 0
        self.processor.record_failed_state_metrics()
        failed_download_after = REGISTRY.get_sample_value(
            'failed_download_total', {'account_number': '1234'})
        self.assertEqual(
            1, int(failed_download_after) - failed_download_before)
