#
# Copyright (c) 2019 Red Hat, Inc.
#
# This software is licensed to you under the GNU General Public License,
# version 3 (GPLv3). There is NO WARRANTY for this software, express or
# implied, including the implied warranties of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. You should have received a copy of GPLv3
# along with this software; if not, see
# https://www.gnu.org/licenses/gpl-3.0.txt.
#

"""Model for forensics."""

from django.db import models


class InventoryUploadError(models.Model):
    """Represents inventory upload error information."""

    report_platform_id = models.UUIDField(null=False)
    report_slice_id = models.UUIDField(null=False)
    request_body = models.TextField(null=False)
    response_body = models.TextField(null=True)
    response_code = models.PositiveSmallIntegerField(null=True)
    rh_account = models.CharField(max_length=50, null=False)
    identity_header = models.TextField(null=False)
    failure_catagory = models.TextField(max_length=64, null=False)
    additional_info = models.TextField(null=True)

    def __str__(self):
        """Convert to string."""
        return '{' + 'report_platform_id:{}, '\
            'report_slice_id: {}, '\
            'request_body: {}, '\
            'response_body: {}, '\
            'rh_account: {}, '\
            'identity_header: {}, '\
            'failure_catagory: {}, '\
            'additional_info: {} '.format(
                self.report_platform_id,
                self.report_slice_id,
                self.request_body,
                self.response_body,
                self.rh_account,
                self.identity_header,
                self.failure_catagory,
                self.additional_info) + '}'
