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
    account = models.CharField(max_length=50, null=False)

    HTTP = 'http'
    KAFKA = 'kafka'
    SOURCE_CHOICES = ((HTTP, HTTP),
                      (KAFKA, KAFKA))

    source = models.CharField(
        max_length=10,
        choices=SOURCE_CHOICES
    )

    details = models.TextField(null=False)

    def __str__(self):
        """Convert to string."""
        return '{' + 'report_platform_id: {}, '\
            'report_slice_id: {}, '\
            'account: {}, '\
            'source: {}, '\
            'details: {}'.format(
                self.report_platform_id,
                self.report_slice_id,
                self.account,
                self.source,
                self.details) + '}'
