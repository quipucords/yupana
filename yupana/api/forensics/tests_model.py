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
"""Test the InventoryUploadError Model."""

import json
import uuid

from django.test import TestCase

from api.models import InventoryUploadError


class InventoryUploadErrorModelTest(TestCase):
    """Tests against the InventoryUploadError model."""

    def setUp(self):
        """Create test case setup."""
        self.uuid = str(uuid.uuid4())
        self.uuid2 = str(uuid.uuid4())
        self.request_body = {
            'host_id': 'foo',
            'ip_addresses': 'bar'
        }
        self.response_body = {
            'foo': 'bar'
        }
        self.identity_header = {
            'account': '123456'
        }
        self.details = {
            'request_body': self.request_body,
            'response_body': self.response_body,
            'response_code': 200,
            'identity_header': self.identity_header,
            'failure_category': 'INVENTORY FAILURE'
        }
        self.inventory_error = InventoryUploadError(
            report_platform_id=self.uuid,
            report_slice_id=self.uuid2,
            account='12345',
            details=json.dumps(self.details),
            upload_type=InventoryUploadError.HTTP,
            source='qpc'
        )
        self.inventory_error.save()

    def test_inventory_upload_error_fields(self):
        """Test the InventoryUploadError fields."""
        self.assertEqual(self.inventory_error.source, 'qpc')
        self.assertEqual(self.inventory_error.upload_type, InventoryUploadError.HTTP)
        self.assertEqual(self.inventory_error.report_platform_id, self.uuid)
        self.assertEqual(self.inventory_error.report_slice_id, self.uuid2)
        self.assertEqual(self.inventory_error.account, '12345')
        self.assertEqual(json.loads(self.inventory_error.details), self.details)
        # pylint: disable=line-too-long
        expected = '{report_platform_id: %s, report_slice_id: %s, account: 12345, source: qpc, upload_type: http, details: {"request_body": {"host_id": "foo", "ip_addresses": "bar"}, "response_body": {"foo": "bar"}, "response_code": 200, "identity_header": {"account": "123456"}, "failure_category": "INVENTORY FAILURE"}}' % (self.uuid, self.uuid2)  # noqa
        self.assertEqual(expected, str(self.inventory_error))
