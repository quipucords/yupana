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
from django.test import TestCase
from processor import (kafka_msg_handler as msg_handler,
                       report_processor,
                       tests_kafka_msg_handler as test_handler)

from api.models import Report, ReportArchive


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
        self.report_record = Report(upload_srv_kafka_msg=json.dumps(self.msg),
                                    rh_account='1234',
                                    state=Report.NEW,
                                    report_json=json.dumps(self.report_json),
                                    state_info=json.dumps([Report.NEW]),
                                    last_update_time=datetime.now(pytz.utc),
                                    candidate_hosts=json.dumps({}),
                                    failed_hosts=json.dumps([]),
                                    retry_count=0)
        self.report_record.save()
        self.processor = report_processor.ReportProcessor()
        self.processor.report = self.report_record

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

    def test_archiving_report(self):
        """Test that archiving creates a ReportArchive, deletes report, and resets the processor."""
        report_to_archive = Report(upload_srv_kafka_msg=json.dumps(self.msg),
                                   rh_account='4321',
                                   report_platform_id=self.uuid2,
                                   state=Report.NEW,
                                   report_json=json.dumps(self.report_json),
                                   state_info=json.dumps([Report.NEW]),
                                   last_update_time=datetime.now(pytz.utc),
                                   candidate_hosts=json.dumps({}),
                                   failed_hosts=json.dumps([]),
                                   retry_count=0)
        report_to_archive.save()
        self.processor.report = report_to_archive
        self.processor.account_number = '4321'
        self.processor.upload_message = self.msg
        self.processor.state = report_to_archive.state
        self.processor.report_id = self.uuid
        self.processor.status = report_processor.SUCCESS_CONFIRM_STATUS
        self.processor.report_json = self.report_json

        self.processor.archive_report()
        # assert the report doesn't exist
        with self.assertRaises(Report.DoesNotExist):
            Report.objects.get(id=report_to_archive.id)
        # assert the report archive does exist
        archived = ReportArchive.objects.get(rh_account='4321')
        self.assertEqual(json.loads(archived.state_info), [Report.NEW])
        # assert the processor was reset
        self.check_variables_are_reset()

    def test_deduplicating_report(self):
        """Test that archiving creates a ReportArchive, deletes report, and resets the processor."""
        self.report_record.report_platform_id = self.uuid
        self.report_record.save()
        report_to_dedup = Report(upload_srv_kafka_msg=json.dumps(self.msg),
                                 rh_account='4321',
                                 report_platform_id=self.uuid,
                                 state=Report.NEW,
                                 report_json=json.dumps(self.report_json),
                                 state_info=json.dumps([Report.NEW]),
                                 last_update_time=datetime.now(pytz.utc),
                                 candidate_hosts=json.dumps({}),
                                 failed_hosts=json.dumps([]),
                                 retry_count=0)
        report_to_dedup.save()
        self.processor.report = report_to_dedup
        self.processor.account_number = '4321'
        self.processor.upload_message = self.msg
        self.processor.state = report_to_dedup.state
        self.processor.report_id = self.uuid
        self.processor.status = report_processor.SUCCESS_CONFIRM_STATUS
        self.processor.report_json = self.report_json

        self.processor.deduplicate_reports()
        # assert the report doesn't exist
        with self.assertRaises(Report.DoesNotExist):
            Report.objects.get(id=report_to_dedup.id)
        # assert the report archive does exist
        archived = ReportArchive.objects.get(rh_account='4321')
        self.assertEqual(json.loads(archived.state_info), [Report.NEW])
        # assert the processor was reset
        self.check_variables_are_reset()

    def test_moved_candidates_to_failed(self):
        """Test that we reset candidates after moving them to failed."""
        candidates = [{self.uuid: {'bios_uuid': 'value', 'name': 'value'}}]
        self.processor.candidate_hosts = candidates
        self.processor.failed_hosts = [
            {self.uuid2: {'bios_uuid': 'value', 'name': 'value'},
             'cause': report_processor.FAILED_VALIDATION}]
        self.processor.move_candidates_to_failed()
        self.assertEqual(self.processor.candidate_hosts, [])
        for host in candidates:
            self.assertIn(host, self.processor.failed_hosts)

    def test_determine_retry_limit(self):
        """Test the determine retry method when the retry is at the limit."""
        candidates = [{self.uuid3: {'ip_addresses': 'value', 'name': 'value'},
                       'cause': report_processor.FAILED_UPLOAD}]
        self.report_record.state = Report.STARTED
        self.report_record.retry_count = 4
        self.report_record.candidate_hosts = json.dumps(candidates)
        self.report_record.save()
        self.processor.report = self.report_record
        self.processor.candidate_hosts = candidates
        self.processor.failed_hosts = []
        self.processor.determine_retry(Report.FAILED_DOWNLOAD,
                                       Report.STARTED)
        self.assertEqual(self.report_record.state, Report.FAILED_DOWNLOAD)
        self.assertEqual(json.loads(self.report_record.candidate_hosts), [])
        for host in candidates:
            self.assertIn(host, json.loads(self.report_record.failed_hosts))

    def test_update_report_state(self):
        """Test updating the report state."""
        # set the base line values
        self.report_record.retry_count = 0
        self.report_record.candidate_hosts = json.dumps([
            {self.uuid4: {'ip_addresses': 'value', 'name': 'value'}}])
        self.report_record.failed_hosts = json.dumps([
            {self.uuid3: {'ip_addresses': 'value', 'name': 'value'},
             'cause': report_processor.FAILED_VALIDATION}])
        self.report_record.save()
        # set the values we will update with
        candidates = [{self.uuid: {'bios_uuid': 'value', 'name': 'value'}}]
        failed = [{self.uuid2: {'bios_uuid': 'value', 'name': 'value'},
                   'cause': report_processor.FAILED_VALIDATION}]
        expected_failed = failed + [
            {self.uuid3: {'ip_addresses': 'value', 'name': 'value'},
             'cause': report_processor.FAILED_VALIDATION}]
        self.processor.report = self.report_record
        self.processor.update_report_state(retry=report_processor.RETRY.increment,
                                           retry_type=Report.GIT_COMMIT,
                                           report_id=self.uuid3,
                                           candidate_hosts=candidates,
                                           failed_hosts=failed)
        self.assertEqual(self.report_record.retry_count, 1)
        self.assertEqual(self.report_record.retry_type, Report.GIT_COMMIT)
        self.assertEqual(self.report_record.report_platform_id, self.uuid3)
        self.assertEqual(json.loads(self.report_record.candidate_hosts), candidates)
        for host in expected_failed:
            self.assertIn(host, json.loads(self.report_record.failed_hosts))

    def test_generate_upload_candidates(self):
        """Test the generate upload candidates method."""
        candidate_hosts = [{self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                            'cause': report_processor.FAILED_UPLOAD,
                            'status_code': '500'},
                           {self.uuid2: {'insights_client_id': 'value', 'name': 'foo'}}]
        expected = {self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                    self.uuid2: {'insights_client_id': 'value', 'name': 'foo'}}
        self.report_record.candidate_hosts = json.dumps(candidate_hosts)
        self.processor.candidate_hosts = candidate_hosts
        candidates_to_upload = self.processor.generate_upload_candidates()
        self.assertEqual(candidates_to_upload, expected)

    async def async_test_run_method(self):
        """Test the run method."""
        self.report_record.state = Report.NEW
        self.processor.report = None

        def delegate_side_effect():
            self.processor.should_run = False

        with patch('processor.report_processor.ReportProcessor.delegate_state',
                   side_effect=delegate_side_effect):
            await self.processor.run()
            self.assertEqual(self.processor.report, self.report_record)

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
        self.processor.assign_report()
        self.assertEqual(self.processor.report, self.report_record)

    def test_assign_report_oldest_time(self):
        """Test the assign report function with older report."""
        current_time = datetime.now(pytz.utc)
        hours_old_time = current_time - timedelta(hours=9)
        older_report = Report(upload_srv_kafka_msg=json.dumps(self.msg),
                              rh_account='4321',
                              report_platform_id=self.uuid2,
                              state=Report.NEW,
                              report_json=json.dumps(self.report_json),
                              state_info=json.dumps([Report.NEW]),
                              last_update_time=hours_old_time,
                              candidate_hosts=json.dumps({}),
                              failed_hosts=json.dumps([]),
                              retry_count=1)
        older_report.save()
        self.report_record.state = Report.NEW
        self.report_record.save()
        self.processor.report = None
        self.processor.assign_report()
        self.assertEqual(self.processor.report, older_report)
        # delete the older report object
        Report.objects.get(id=older_report.id).delete()

    def test_assign_report_not_old_enough(self):
        """Test the assign report function with young report."""
        # delete the report record
        Report.objects.get(id=self.report_record.id).delete()
        self.processor.report = None
        current_time = datetime.now(pytz.utc)
        min_old_time = current_time - timedelta(minutes=1)
        older_report = Report(upload_srv_kafka_msg=json.dumps(self.msg),
                              rh_account='4321',
                              report_platform_id=self.uuid2,
                              state=Report.STARTED,
                              report_json=json.dumps(self.report_json),
                              state_info=json.dumps([Report.NEW]),
                              last_update_time=min_old_time,
                              candidate_hosts=json.dumps({}),
                              failed_hosts=json.dumps([]),
                              retry_count=1)
        older_report.save()
        self.processor.assign_report()
        self.assertEqual(self.processor.report, None)
        # delete the older report object
        Report.objects.get(id=older_report.id).delete()

    def test_assign_report_oldest_commit(self):
        """Test the assign report function with retry type as commit."""
        current_time = datetime.now(pytz.utc)
        twentyminold_time = current_time - timedelta(minutes=20)
        older_report = Report(upload_srv_kafka_msg=json.dumps(self.msg),
                              rh_account='4321',
                              report_platform_id=self.uuid2,
                              state=Report.DOWNLOADED,
                              report_json=json.dumps(self.report_json),
                              state_info=json.dumps([Report.NEW,
                                                     Report.DOWNLOADED]),
                              last_update_time=twentyminold_time,
                              candidate_hosts=json.dumps({}),
                              failed_hosts=json.dumps([]),
                              retry_count=1,
                              retry_type=Report.GIT_COMMIT,
                              git_commit='1234')
        older_report.save()
        self.report_record.state = Report.DOWNLOADED
        self.report_record.save()
        self.processor.report = None
        # the commit should always be different from 1234
        self.processor.assign_report()
        self.assertEqual(self.processor.report, older_report)
        self.assertEqual(self.processor.report.state, Report.DOWNLOADED)
        # delete the older report object
        Report.objects.get(id=older_report.id).delete()

    def test_assign_report_no_reports(self):
        """Test the assign report method with no reports."""
        # delete the report record
        Report.objects.get(id=self.report_record.id).delete()
        self.processor.report = None
        self.processor.assign_report()
        self.assertEqual(self.processor.report, None)

    async def async_test_delegate_state(self):
        """Set up the test for delegate state."""
        self.report_record.state = Report.STARTED
        self.report_record.report_platform_id = self.uuid
        self.report_record.upload_ack_status = report_processor.SUCCESS_CONFIRM_STATUS
        self.report_record.save()
        self.processor.report = self.report_record

        def download_side_effect():
            """Transition the state to downloaded."""
            self.report_record.state = Report.DOWNLOADED
            self.report_record.save()
        with patch('processor.report_processor.ReportProcessor.transition_to_downloaded',
                   side_effect=download_side_effect):
            await self.processor.delegate_state()
            self.assertEqual(self.processor.report_id, self.report_record.report_platform_id)
            self.assertEqual(self.processor.report.state, Report.DOWNLOADED)
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

    def test_reinit_variables(self):
        """Test that reinitting the variables clears the values."""
        # make sure that the variables have values
        self.processor.report_id = self.uuid
        self.processor.report = self.report_record
        self.processor.state = Report.NEW
        self.processor.account_number = '1234'
        self.processor.upload_message = self.msg
        self.processor.report_json = {}
        self.processor.candidate_hosts = []
        self.processor.failed_hosts = []
        self.processor.status = report_processor.SUCCESS_CONFIRM_STATUS
        self.assertEqual(self.processor.report, self.report_record)
        self.assertEqual(self.processor.report_id, self.uuid)
        self.assertEqual(self.processor.state, Report.NEW)
        self.assertEqual(self.processor.account_number, '1234')
        self.assertEqual(self.processor.upload_message, self.msg)
        self.assertEqual(self.processor.report_json, {})
        self.assertEqual(self.processor.candidate_hosts, [])
        self.assertEqual(self.processor.failed_hosts, [])
        self.assertEqual(self.processor.status, report_processor.SUCCESS_CONFIRM_STATUS)

        # check all of the variables are None after reinitting
        self.processor.reset_variables()
        self.check_variables_are_reset()

    def test_transition_to_started(self):
        """Test the transition to started state."""
        self.report_record.state = Report.NEW
        self.processor.report = self.report_record
        self.processor.transition_to_started()
        self.assertEqual(self.report_record.state, Report.STARTED)
        self.assertEqual(json.loads(self.report_record.state_info), [Report.NEW, Report.STARTED])

    def test_transition_to_downloaded(self):
        """Test that the transition to download works successfully."""
        report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'insights_client_id': 'value'}}}
        self.processor.upload_message = {'url': self.payload_url, 'rh_account': '00001'}
        test_dict = dict()
        test_dict['file.json'] = report_json
        buffer_content = test_handler.create_tar_buffer(test_dict)
        with requests_mock.mock() as mock_req:
            mock_req.get(self.payload_url, content=buffer_content)
            self.processor.transition_to_downloaded()
            self.assertEqual(self.report_record.state, Report.DOWNLOADED)

    def test_transition_to_downloaded_exception_retry(self):
        """Test that the transition to download with retry exception."""
        self.processor.upload_message = {'url': self.payload_url, 'rh_account': '00001'}
        self.report_record.state = Report.STARTED
        self.report_record.save()
        self.processor.report = self.report_record
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
        self.processor.report = self.report_record

        def download_side_effect():
            """Raise a FailDownloadException."""
            raise report_processor.FailDownloadException()

        with patch('processor.report_processor.ReportProcessor._download_report',
                   side_effect=download_side_effect):
            self.processor.transition_to_downloaded()
            self.assertEqual(self.report_record.state, Report.FAILED_DOWNLOAD)

    def test_transition_to_validated(self):
        """Test that the transition to validated is successful."""
        self.processor.report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'bios_uuid': 'value'}}}
        self.processor.transition_to_validated()
        self.assertEqual(self.report_record.state, Report.VALIDATED)

    def test_transition_to_validated_report_exception(self):
        """Test that a report with invalid type is still marked as validated."""
        self.report_record.state = Report.DOWNLOADED
        self.report_record.save()
        self.processor.report = self.report_record
        self.processor.report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'key': 'value'}}}
        self.processor.transition_to_validated()
        self.assertEqual(self.report_record.state, Report.VALIDATED)
        self.assertEqual(self.report_record.upload_ack_status,
                         report_processor.FAILURE_CONFIRM_STATUS)
        self.assertEqual(self.report_record.retry_count, 0)

    def test_transition_to_validated_general_exception(self):
        """Test that when a general exception is raised, we don't pass validation."""
        self.report_record.state = Report.DOWNLOADED
        self.report_record.save()
        self.processor.report = self.report_record
        self.processor.report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'key': 'value'}}}

        def validate_side_effect():
            """Transition the state to downloaded."""
            raise Exception('Test')

        with patch('processor.report_processor.ReportProcessor._validate_report_details',
                   side_effect=validate_side_effect):
            self.processor.transition_to_validated()
            self.assertEqual(self.report_record.state, Report.DOWNLOADED)
            self.assertEqual(self.report_record.retry_count, 1)

    async def async_test_transition_to_validation_reported(self):
        """Set up the test for transitioning to validation reported."""
        self.report_record.state = Report.VALIDATED
        self.report_record.report_platform_id = self.uuid
        self.report_record.upload_ack_status = report_processor.SUCCESS_CONFIRM_STATUS
        self.report_record.save()
        self.processor.report = self.report_record
        self.processor.status = report_processor.SUCCESS_CONFIRM_STATUS
        self.processor.upload_message = {'hash': self.uuid}

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
        self.processor.report = self.report_record
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
        report_to_archive = Report(upload_srv_kafka_msg=json.dumps(self.msg),
                                   rh_account='43214',
                                   report_platform_id=self.uuid2,
                                   state=Report.VALIDATED,
                                   report_json=json.dumps(self.report_json),
                                   state_info=json.dumps([Report.NEW]),
                                   last_update_time=datetime.now(pytz.utc),
                                   candidate_hosts=json.dumps([]),
                                   failed_hosts=json.dumps([]),
                                   retry_count=0,
                                   retry_type=Report.TIME)
        report_to_archive.upload_ack_status = report_processor.FAILURE_CONFIRM_STATUS
        report_to_archive.save()
        self.processor.report = report_to_archive
        self.processor.report_id = self.uuid2
        self.processor.account_number = '43214'
        self.processor.state = Report.VALIDATED
        self.processor.status = report_processor.FAILURE_CONFIRM_STATUS
        self.processor.upload_message = {'hash': self.uuid}
        self.processor._send_confirmation = CoroutineMock()
        await self.processor.transition_to_validation_reported()
        with self.assertRaises(Report.DoesNotExist):
            Report.objects.get(id=report_to_archive.id)
        archived = ReportArchive.objects.get(rh_account='43214')
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

    def test_transition_to_hosts_uploaded(self):
        """Test the transition to hosts being uploaded."""
        hosts = [{self.uuid: {'bios_uuid': 'value', 'name': 'value'}},
                 {self.uuid2: {'insights_client_id': 'value', 'name': 'foo'}},
                 {self.uuid3: {'ip_addresses': 'value', 'name': 'foo'}},
                 {self.uuid4: {'mac_addresses': 'value', 'name': 'foo'}},
                 {self.uuid5: {'vm_uuid': 'value', 'name': 'foo'}},
                 {self.uuid6: {'etc_machine_id': 'value'}},
                 {self.uuid7: {'subscription_manager_id': 'value'}}]
        self.report_record.failed_hosts = []
        self.report_record.candidate_hosts = json.dumps(hosts)
        self.report_record.save()
        self.processor.report = self.report_record
        self.processor.candidate_hosts = hosts
        with patch('processor.report_processor.ReportProcessor._upload_to_host_inventory',
                   return_value=([], [])):
            self.processor.transition_to_hosts_uploaded()
            self.assertEqual(json.loads(self.report_record.candidate_hosts), [])
            self.assertEqual(self.report_record.state, Report.HOSTS_UPLOADED)

    def test_transition_to_hosts_uploaded_unsuccessful(self):
        """Test the transition to hosts being uploaded."""
        hosts = [{self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                  'cause': report_processor.FAILED_UPLOAD,
                  'status_code': '500'},
                 {self.uuid2: {'insights_client_id': 'value', 'name': 'foo'}},
                 {self.uuid3: {'ip_addresses': 'value', 'name': 'foo'}},
                 {self.uuid4: {'mac_addresses': 'value', 'name': 'foo'}},
                 {self.uuid5: {'vm_uuid': 'value', 'name': 'foo'}},
                 {self.uuid6: {'etc_machine_id': 'value'}}]
        retry_commit_hosts = [{self.uuid7: {'subscription_manager_id': 'value'},
                               'cause': report_processor.FAILED_UPLOAD,
                               'status_code': '400'}]
        self.report_record.failed_hosts = []
        self.report_record.candidate_hosts = json.dumps(hosts)
        self.report_record.save()
        self.processor.report = self.report_record
        self.processor.candidate_hosts = hosts
        with patch('processor.report_processor.ReportProcessor._upload_to_host_inventory',
                   return_value=(hosts, retry_commit_hosts)):
            self.processor.transition_to_hosts_uploaded()
            total_hosts = hosts + retry_commit_hosts
            for host in total_hosts:
                self.assertIn(host, json.loads(self.report_record.candidate_hosts))
            self.assertEqual(self.report_record.state, Report.VALIDATION_REPORTED)
            self.assertEqual(self.report_record.retry_count, 1)

    def test_transition_to_hosts_uploaded_no_candidates(self):
        """Test the transition to hosts being uploaded."""
        faulty_report = Report(upload_srv_kafka_msg=json.dumps(self.msg),
                               rh_account='987',
                               report_platform_id=self.uuid2,
                               state=Report.VALIDATION_REPORTED,
                               report_json=json.dumps(self.report_json),
                               state_info=json.dumps([Report.NEW, Report.STARTED,
                                                      Report.VALIDATION_REPORTED]),
                               last_update_time=datetime.now(pytz.utc),
                               candidate_hosts=json.dumps({}),
                               failed_hosts=json.dumps([]),
                               retry_count=0)
        faulty_report.save()
        self.processor.report = faulty_report
        self.processor.account_number = '987'
        self.processor.upload_message = self.msg
        self.processor.state = faulty_report.state
        self.processor.report_id = self.uuid2
        self.processor.status = report_processor.SUCCESS_CONFIRM_STATUS
        self.processor.report_json = self.report_json
        self.processor.candidate_hosts = {}
        self.processor.transition_to_hosts_uploaded()
        archived = ReportArchive.objects.get(rh_account='987')
        with self.assertRaises(Report.DoesNotExist):
            Report.objects.get(id=faulty_report.id)
        self.assertEqual(json.loads(archived.state_info),
                         [Report.NEW, Report.STARTED, Report.VALIDATION_REPORTED])
        # assert the processor was reset
        self.check_variables_are_reset()

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

        def hosts_upload_side_effect():
            raise Exception('Test')

        with patch('processor.report_processor.ReportProcessor._upload_to_host_inventory',
                   side_effect=hosts_upload_side_effect):
            self.processor.transition_to_hosts_uploaded()
            self.assertEqual(self.report_record.state, Report.VALIDATION_REPORTED)
            self.assertEqual(self.report_record.retry_count, 1)

    # Tests for the functions that carry out the work ie (downlaod/upload)
    def test_validate_report_success(self):
        """Test that a QPC report with the correct structure passes validation."""
        self.processor.report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'bios_uuid': 'value'}}}
        valid, invalid = self.processor._validate_report_details()
        expect_valid = [{self.uuid: {'bios_uuid': 'value'}}]
        expect_invalid = []
        self.assertEqual(valid, expect_valid)
        self.assertEqual(invalid, expect_invalid)

    def test_validate_report_success_mixed_hosts(self):
        """Test that a QPC report with the correct structure passes validation."""
        self.processor.report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'bios_uuid': 'value'},
                      self.uuid2: {'invalid': 'value'}}}

        valid, invalid = self.processor._validate_report_details()
        expect_valid = [{self.uuid: {'bios_uuid': 'value'}}]
        expect_invalid = [{self.uuid2: {'invalid': 'value'},
                           'cause': report_processor.FAILED_VALIDATION}]
        self.assertEqual(valid, expect_valid)
        self.assertEqual(invalid, expect_invalid)

    def test_validate_report_missing_id(self):
        """Test that a QPC report with a missing id is fails validation."""
        self.processor.report_json = {
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'key': 'value'}}}

        with self.assertRaises(msg_handler.QPCReportException):
            _, _ = self.processor._validate_report_details()

    def test_validate_report_fails_no_canonical_facts(self):
        """Test to verify a QPC report with the correct structure passes validation."""
        self.processor.report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'name': 'value'}}}

        with self.assertRaises(msg_handler.QPCReportException):
            _, _ = self.processor._validate_report_details()

    def test_validate_report_invalid_report_type(self):
        """Test to verify a QPC report with an invalid report_type is failed."""
        self.processor.report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'key': 'value'}}}

        with self.assertRaises(msg_handler.QPCReportException):
            _, _ = self.processor._validate_report_details()

    def test_validate_report_missing_version(self):
        """Test to verify a QPC report missing report_version is failed."""
        self.processor.report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'key': 'value'}}}

        with self.assertRaises(msg_handler.QPCReportException):
            _, _ = self.processor._validate_report_details()

    def test_validate_report_missing_platform_id(self):
        """Test to verify a QPC report missing report_platform_id is failed."""
        self.processor.report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'hosts': {self.uuid: {'key': 'value'}}}

        with self.assertRaises(msg_handler.QPCReportException):
            _, _ = self.processor._validate_report_details()

    def test_validate_report_missing_hosts(self):
        """Test to verify a QPC report with empty hosts is failed."""
        self.processor.report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {}}

        with self.assertRaises(msg_handler.QPCReportException):
            _, _ = self.processor._validate_report_details()

    def test_validate_report_invalid_hosts(self):
        """Test to verify a QPC report with invalid hosts is failed."""
        self.processor.report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': ['foo']}

        with self.assertRaises(msg_handler.QPCReportException):
            _, _ = self.processor._validate_report_details()

    def test_validate_report_invalid_hosts_key(self):
        """Test to verify a QPC report with invalid hosts key is failed."""
        self.processor.report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {1: {'foo': 'bar'}}}

        with self.assertRaises(msg_handler.QPCReportException):
            _, _ = self.processor._validate_report_details()

    def test_validate_report_invalid_hosts_val(self):
        """Test to verify a QPC report with invalid hosts value is failed."""
        self.processor.report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: ['foo']}}

        with self.assertRaises(msg_handler.QPCReportException):
            _, _ = self.processor._validate_report_details()

    def test_validate_report_hosts(self):
        """Test host verification."""
        # test all valid hosts
        uuid8 = str(uuid.uuid4())
        uuid9 = str(uuid.uuid4())

        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo'},
                 self.uuid3: {'ip_addresses': 'value', 'name': 'foo'},
                 self.uuid4: {'mac_addresses': 'value', 'name': 'foo'},
                 self.uuid5: {'vm_uuid': 'value', 'name': 'foo'},
                 self.uuid6: {'etc_machine_id': 'value'},
                 self.uuid7: {'subscription_manager_id': 'value'},
                 uuid8: {'not_valid': 'value'}
                 }
        expected_valid = [{self.uuid: {'bios_uuid': 'value', 'name': 'value'}},
                          {self.uuid2: {'insights_client_id': 'value', 'name': 'foo'}},
                          {self.uuid3: {'ip_addresses': 'value', 'name': 'foo'}},
                          {self.uuid4: {'mac_addresses': 'value', 'name': 'foo'}},
                          {self.uuid5: {'vm_uuid': 'value', 'name': 'foo'}},
                          {self.uuid6: {'etc_machine_id': 'value'}},
                          {self.uuid7: {'subscription_manager_id': 'value'}}]
        expected_invalid = [{uuid8: {'not_valid': 'value'},
                             'cause': report_processor.FAILED_VALIDATION}]
        self.processor.report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': hosts}
        actual_valid, actual_invalid = self.processor._validate_report_hosts()
        self.assertEqual(actual_valid, expected_valid)
        self.assertEqual(actual_invalid, expected_invalid)

        # test that invalid hosts are removed
        invalid_host = {uuid9: {'no': 'canonical facts', 'metadata': []}}
        hosts.update(invalid_host)
        valid_hosts, _ = self.processor._validate_report_hosts()
        self.assertEqual(valid_hosts, expected_valid)

        # test that if there are no valid hosts we return []
        self.processor.report_json['hosts'] = invalid_host
        valid_hosts, _ = self.processor._validate_report_hosts()
        self.assertEqual([], valid_hosts)

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
        buffer_content = test_handler.create_tar_buffer(test_dict)
        result = self.processor._extract_report_from_tar_gz(buffer_content)
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
        buffer_content = test_handler.create_tar_buffer(test_dict)
        with self.assertRaises(report_processor.FailExtractException):
            self.processor._extract_report_from_tar_gz(buffer_content)

    def test_extract_report_from_tar_gz_failure_no_json(self):
        """Testing the extract method failure no json file."""
        report_json = 'No valid report'
        test_dict = dict()
        test_dict['file.txt'] = report_json
        buffer_content = test_handler.create_tar_buffer(test_dict)
        with self.assertRaises(report_processor.FailExtractException):
            self.processor._extract_report_from_tar_gz(buffer_content)

    def test_extract_report_from_tar_gz_failure_invalid_json(self):
        """Testing the extract method failure invalid json."""
        report_json = None
        test_dict = dict()
        test_dict['file.json'] = report_json
        buffer_content = test_handler.create_tar_buffer(test_dict)
        with self.assertRaises(report_processor.FailExtractException):
            self.processor._extract_report_from_tar_gz(buffer_content)

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
        report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': {self.uuid: {'insights_client_id': 'value'}}}
        self.processor.upload_message = {'url': self.payload_url, 'rh_account': '00001'}
        test_dict = dict()
        test_dict['file.json'] = report_json
        buffer_content = test_handler.create_tar_buffer(test_dict)
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
            'hosts': {self.uuid: {'key': 'value'}}}
        self.processor.upload_message = {'url': self.payload_url, 'rh_account': '00001'}
        test_dict = dict()
        test_dict['file.json'] = report_json
        buffer_content = test_handler.create_tar_buffer(test_dict)
        with requests_mock.mock() as mock_req:
            mock_req.get(self.payload_url, content=buffer_content)
            with patch('requests.get', side_effect=requests.exceptions.HTTPError):
                with self.assertRaises(report_processor.RetryDownloadException):
                    content = self.processor._download_report()
                    self.assertEqual(content, buffer_content)

    def test_download_with_404(self):
        """Test downloading a URL and getting 404."""
        report_json = {}
        test_dict = dict()
        test_dict['file.json'] = report_json
        with requests_mock.mock() as mock_req:
            mock_req.get(self.payload_url, status_code=404)
            with self.assertRaises(report_processor.RetryDownloadException):
                self.processor.upload_message = {'url': self.payload_url}
                self.processor._download_report()

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
        with self.assertRaises(report_processor.FailExtractException):
            self.processor._extract_report_from_tar_gz(buffer_content)

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
        with self.assertRaises(report_processor.FailExtractException):
            self.processor._extract_report_from_tar_gz(buffer_content)

    def test_extract_report_from_tar_gz_general_except(self):
        """Testing general exception raises retry exception."""
        def extract_side_effect():
            """Raise general error."""
            raise Exception('Test')

        with patch('processor.report_processor.tarfile.open',
                   side_effect=extract_side_effect):
            with self.assertRaises(report_processor.RetryExtractException):
                self.processor._extract_report_from_tar_gz(None)

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
            mock_req.post(report_processor.INSIGHTS_HOST_INVENTORY_URL,
                          status_code=207, json=bulk_response)
            retry_time_hosts, retry_commit_hosts = \
                self.processor._upload_to_host_inventory(hosts)
            self.assertEqual(retry_time_hosts, [])
            self.assertEqual(retry_commit_hosts, [])

    def test_no_json_resp_host_inventory_upload(self):
        """Testing unsuccessful upload to host inventory."""
        self.processor.account_number = self.uuid
        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo'}}

        expected_hosts = [{self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                           'cause': report_processor.FAILED_UPLOAD},
                          {self.uuid2: {'insights_client_id': 'value', 'name': 'foo'},
                           'cause': report_processor.FAILED_UPLOAD}]
        with requests_mock.mock() as mock_req:
            mock_req.post(report_processor.INSIGHTS_HOST_INVENTORY_URL,
                          status_code=207, json=None)
            retry_time_hosts, retry_commit_hosts = \
                self.processor._upload_to_host_inventory(hosts)
            for host in expected_hosts:
                self.assertIn(host, retry_time_hosts)
            self.assertEqual(retry_commit_hosts, [])

    def test_400_resp_host_inventory_upload(self):
        """Testing unsuccessful upload to host inventory."""
        self.processor.account_number = self.uuid
        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo'}}

        expected_hosts = [{self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                           'cause': report_processor.FAILED_UPLOAD},
                          {self.uuid2: {'insights_client_id': 'value', 'name': 'foo'},
                           'cause': report_processor.FAILED_UPLOAD}]
        with requests_mock.mock() as mock_req:
            mock_req.post(report_processor.INSIGHTS_HOST_INVENTORY_URL,
                          status_code=400, json=None)
            retry_time_hosts, retry_commit_hosts = \
                self.processor._upload_to_host_inventory(hosts)
            for host in expected_hosts:
                self.assertIn(host, retry_commit_hosts)
            self.assertEqual(retry_time_hosts, [])

    def test_500_resp_host_inventory_upload(self):
        """Testing unsuccessful upload to host inventory."""
        self.processor.account_number = self.uuid
        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo'}}

        expected_hosts = [{self.uuid: {'bios_uuid': 'value', 'name': 'value'},
                           'cause': report_processor.FAILED_UPLOAD},
                          {self.uuid2: {'insights_client_id': 'value', 'name': 'foo'},
                           'cause': report_processor.FAILED_UPLOAD}]
        with requests_mock.mock() as mock_req:
            mock_req.post(report_processor.INSIGHTS_HOST_INVENTORY_URL,
                          status_code=500, json=None)
            retry_time_hosts, retry_commit_hosts = \
                self.processor._upload_to_host_inventory(hosts)
            for host in expected_hosts:
                self.assertIn(host, retry_time_hosts)
            self.assertEqual(retry_commit_hosts, [])

    def test_host_inventory_upload_500(self):
        """Testing successful upload to host inventory with 500 errors."""
        self.processor.account_number = self.uuid
        hosts = {self.uuid: {'bios_uuid': 'value', 'name': 'value',
                             'system_platform_id': self.uuid},
                 self.uuid2: {'insights_client_id': 'value', 'name': 'foo',
                              'system_platform_id': self.uuid2}}
        expected_hosts = [{self.uuid: {'bios_uuid': 'value', 'name': 'value',
                                       'system_platform_id': self.uuid},
                           'cause': report_processor.FAILED_UPLOAD,
                           'status_code': 500},
                          {self.uuid2: {'insights_client_id': 'value', 'name': 'foo',
                                        'system_platform_id': self.uuid2},
                           'cause': report_processor.FAILED_UPLOAD,
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
            mock_req.post(report_processor.INSIGHTS_HOST_INVENTORY_URL,
                          status_code=207, json=bulk_response)
            retry_time, retry_commit = self.processor._upload_to_host_inventory(hosts)
            self.assertEqual(retry_commit, [])
            for host in expected_hosts:
                self.assertIn(host, retry_time)

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
                           'cause': report_processor.FAILED_UPLOAD,
                           'status_code': 400},
                          {self.uuid2: {'insights_client_id': 'value', 'name': 'foo',
                                        'system_platform_id': self.uuid2},
                           'cause': report_processor.FAILED_UPLOAD,
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
            mock_req.post(report_processor.INSIGHTS_HOST_INVENTORY_URL,
                          status_code=207, json=bulk_response)
            retry_time_hosts, retry_commit_hosts = \
                self.processor._upload_to_host_inventory(hosts)
            self.assertEqual(retry_time_hosts, [])
            for host in expected_hosts:
                self.assertIn(host, retry_commit_hosts)

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
        with patch('processor.report_processor.INSIGHTS_HOST_INVENTORY_URL', value='not none'):
            self.processor._upload_to_host_inventory(hosts)

    def test_format_certs(self):
        """Testing the format_certs function."""
        certs = ['69.pem', '67.pem']
        formatted_certs = self.processor.format_certs(certs)
        self.assertEqual([69, 67], formatted_certs)
        # assert empty list stays emtpy
        certs = []
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
