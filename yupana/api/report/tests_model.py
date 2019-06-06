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
"""Test the Report API."""

import uuid
from datetime import datetime

from django.test import TestCase

from api.report.model import Report


class ReportModelTest(TestCase):
    """Tests against the Report model."""

    def setUp(self):
        """Create test case setup."""
        self.uuid = str(uuid.uuid4())
        self.upload_srv_msg = {'accountid': '13423',
                               'msg_url': 'foo'}
        self.date = datetime.now()
        self.report = Report(report_platform_id=self.uuid,
                             upload_srv_kafka_msg=self.upload_srv_msg,
                             rh_account='13423',
                             state=Report.NEW,
                             state_info=[Report.NEW],
                             retry_count=0,
                             last_update_time=self.date,
                             ready_to_archive=False)

    def test_report_fields(self):
        """Test the report fields."""
        self.assertEqual(self.report.report_platform_id, self.uuid)
        self.assertEqual(self.report.upload_srv_kafka_msg,
                         self.upload_srv_msg)
        self.assertEqual(self.report.ready_to_archive, False)
        self.assertEqual(self.report.state, Report.NEW)
        self.assertEqual(self.report.state_info, [Report.NEW])
        self.assertEqual(self.report.last_update_time, self.date)
        # pylint: disable=line-too-long
        expected = "{report_platform_id:%s, host_inventory_api_version: None, source: None, source_metadata: None, rh_account: 13423, upload_ack_status: None, upload_srv_kafka_msg: {'accountid': '13423', 'msg_url': 'foo'}, git_commit: None, state: new, state_info: ['new'], retry_count: 0, retry_type: time, last_update_time: %s }" % (self.uuid, self.date)  # noqa
        self.assertEqual(str(self.report), expected)
