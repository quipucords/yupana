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
        self.report_json = {'report_platform_id': self.uuid,
                            'report_type': 'insights',
                            'hosts': {}}
        self.date = datetime.now()
        self.report = Report(report_platform_id=self.uuid,
                             upload_srv_kafka_msg=self.upload_srv_msg,
                             rh_account='13423',
                             report_json=self.report_json,
                             state=Report.NEW,
                             state_info=[Report.NEW],
                             retry_count=0,
                             last_update_time=self.date,
                             failed_hosts=[],
                             candidate_hosts=[])

    def test_report_fields(self):
        """Test the report fields."""
        self.assertEqual(self.report.report_platform_id, self.uuid)
        self.assertEqual(self.report.upload_srv_kafka_msg,
                         self.upload_srv_msg)
        self.assertEqual(self.report.report_json, self.report_json)
        self.assertEqual(self.report.state, Report.NEW)
        self.assertEqual(self.report.state_info, [Report.NEW])
        self.assertEqual(self.report.last_update_time, self.date)
        expected = "{report_platform_id:%s, rh_account: 13423, upload_srv_kafka_msg:{'accountid': '13423', 'msg_url': 'foo'}, report_json:{'report_platform_id': '%s', 'report_type': 'insights', 'hosts': {}}, state:new, state_info:['new'], retry_count:0, last_update_time:%s, failed_hosts:[], candidate_hosts:[] }" % (self.uuid, self.uuid, self.date)  # noqa
        self.assertEqual(str(self.report), expected)
