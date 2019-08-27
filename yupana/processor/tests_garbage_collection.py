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
"""Tests the garbage collector."""

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytz
from asynctest import CoroutineMock
from django.test import TestCase
from processor import garbage_collection

from api.models import (Report,
                        ReportArchive,
                        ReportSlice,
                        ReportSliceArchive)


class GarbageCollectorTests(TestCase):
    """Test Cases for the garbage collector."""

    def setUp(self):
        """Create test setup."""
        self.uuid = uuid.uuid4()
        self.uuid2 = uuid.uuid4()
        self.report_json = {
            'report_id': 1,
            'report_type': 'insights',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'hosts': [{'bios_uuid': 'value'},
                      {'invalid': 'value'}]}
        self.report_record = ReportArchive(
            upload_srv_kafka_msg=json.dumps({}),
            account='1234',
            state=Report.NEW,
            state_info=json.dumps([Report.NEW]),
            last_update_time=datetime.now(pytz.utc),
            retry_count=0,
            ready_to_archive=False,
            source='satellite',
            arrival_time=datetime.now(pytz.utc),
            processing_start_time=datetime.now(pytz.utc),
            processing_end_time=datetime.now(pytz.utc))
        self.report_record.save()

        self.report_slice = ReportSliceArchive(
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
            processing_start_time=datetime.now(pytz.utc),
            processing_end_time=datetime.now(pytz.utc))
        self.report_slice.save()
        self.report_record.save()
        self.garbage_collector = garbage_collection.GarbageCollector()

    def test_deleting_archive(self):
        """Test deleting the report archive."""
        current_time = datetime.now(pytz.utc)
        weeks_old_time = current_time - timedelta(weeks=5)
        archive_to_delete = ReportArchive(
            upload_srv_kafka_msg=json.dumps({}),
            account='4321',
            report_platform_id=self.uuid2,
            state=Report.NEW,
            state_info=json.dumps([Report.NEW]),
            last_update_time=datetime.now(pytz.utc),
            retry_count=0,
            ready_to_archive=True,
            arrival_time=datetime.now(pytz.utc),
            processing_start_time=datetime.now(pytz.utc),
            processing_end_time=weeks_old_time)
        archive_to_delete.save()
        self.garbage_collector.collect_the_garbage()
        # assert the report doesn't exist
        with self.assertRaises(ReportArchive.DoesNotExist):
            ReportArchive.objects.get(id=archive_to_delete.id)

    def test_deleting_archive_not_ready(self):
        """Test that delete fails if archive not ready."""
        current_time = datetime.now(pytz.utc)
        week_old_time = current_time - timedelta(weeks=1)
        archive_to_delete = ReportArchive(
            upload_srv_kafka_msg=json.dumps({}),
            account='4321',
            report_platform_id=self.uuid2,
            state=Report.NEW,
            state_info=json.dumps([Report.NEW]),
            last_update_time=datetime.now(pytz.utc),
            retry_count=0,
            ready_to_archive=True,
            arrival_time=datetime.now(pytz.utc),
            processing_start_time=datetime.now(pytz.utc),
            processing_end_time=week_old_time)
        archive_to_delete.save()
        self.garbage_collector.collect_the_garbage()
        # assert the report still exist
        existing_report = ReportArchive.objects.get(id=archive_to_delete.id)
        self.assertEqual(existing_report, archive_to_delete)
