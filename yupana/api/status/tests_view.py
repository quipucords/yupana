#
# Copyright 2018 Red Hat, Inc.
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
"""Test the status API."""


from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse


class StatusViewTest(TestCase):
    """Tests the status view."""

    def test_status_endpoint(self):
        """Test the status endpoint."""
        url = reverse('server-status')
        response = self.client.get(url)
        json_result = response.json()
        self.assertEqual(json_result['api_version'], 1)

    @patch('api.status.view.list_name_of_processors')
    def test_test_test(self, mock_active_processor):
        """Test the status endpoint."""
        mock_active_processor.return_value = ['report_consumer', 'report_processor']
        url = reverse('server-status')
        res = self.client.get(url)
        self.assertEqual(res.status_code, 500)
