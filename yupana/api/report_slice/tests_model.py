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

from api.report_slice.model import ReportSlice


class ReportSliceModelTest(TestCase):
    """Tests against the ReportSlice model."""

    def setUp(self):
        """Create test case setup."""
        self.uuid = str(uuid.uuid4())
        self.upload_srv_msg = {'accountid': '13423',
                               'msg_url': 'foo'}
        self.report_json = {'report_platform_id': self.uuid,
                            'report_type': 'insights',
                            'hosts': {}}
        self.date = datetime.now()
        self.report = ReportSlice(
            report_platform_id=self.uuid,
            upload_srv_kafka_msg=self.upload_srv_msg,
            rh_account='13423',
            report_json=self.report_json,
            state=ReportSlice.NEW,
            state_info=[ReportSlice.NEW],
            retry_count=0,
            last_update_time=self.date,
            failed_hosts=[],
            candidate_hosts=[])

    def test_report_fields(self):
        """Test the report slice fields."""
        self.assertEqual(self.report.report_platform_id, self.uuid)
        self.assertEqual(self.report.upload_srv_kafka_msg,
                         self.upload_srv_msg)
        self.assertEqual(self.report.report_json, self.report_json)
        self.assertEqual(self.report.state, ReportSlice.NEW)
        self.assertEqual(self.report.state_info, [ReportSlice.NEW])
        self.assertEqual(self.report.last_update_time, self.date)
        # pylint: disable=line-too-long
        expected = "{report_platform_id:%s, rh_account: 13423, upload_ack_status: None, upload_srv_kafka_msg:{'accountid': '13423', 'msg_url': 'foo'}, report_json:{'report_platform_id': '%s', 'report_type': 'insights', 'hosts': {}}, git_commit:None, state:new, state_info:['new'], retry_count:0, retry_type:time, last_update_time:%s, failed_hosts:[], candidate_hosts:[] }" % (self.uuid, self.uuid, self.date)  # noqa
        self.assertEqual(str(self.report), expected)
