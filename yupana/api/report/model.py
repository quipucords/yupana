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

"""Model for report progress."""

from django.db import models


class AbstractReport(models.Model):
    """Represents report information."""

    report_platform_id = models.UUIDField(null=True)
    host_inventory_api_version = models.CharField(max_length=10, null=True)
    source = models.CharField(max_length=15, null=True)
    source_metadata = models.TextField(null=True)
    account = models.CharField(max_length=50, null=True)
    request_id = models.CharField(max_length=50, null=True)
    upload_ack_status = models.CharField(max_length=10, null=True)
    upload_srv_kafka_msg = models.TextField(null=True)
    git_commit = models.CharField(max_length=50, null=True)
    ready_to_archive = models.BooleanField(null=False, default=False)

    NEW = 'new'
    STARTED = 'started'
    DOWNLOADED = 'downloaded'
    FAILED_DOWNLOAD = 'failed to download'
    VALIDATED = 'validated'
    FAILED_VALIDATION = 'failed validation'
    VALIDATION_REPORTED = 'validation reported'
    FAILED_VALIDATION_REPORTING = 'failed to report validation'
    STATE_CHOICES = ((NEW, NEW),
                     (STARTED, STARTED),
                     (DOWNLOADED, DOWNLOADED),
                     (FAILED_DOWNLOAD, FAILED_DOWNLOAD),
                     (VALIDATED, VALIDATED),
                     (FAILED_VALIDATION, FAILED_VALIDATION),
                     (VALIDATION_REPORTED, VALIDATION_REPORTED),
                     (FAILED_VALIDATION_REPORTING, FAILED_VALIDATION_REPORTING))

    state = models.CharField(
        max_length=28,
        choices=STATE_CHOICES,
        default=NEW
    )

    TIME = 'time'
    GIT_COMMIT = 'git commit'
    RETRY_CHOICES = ((TIME, TIME),
                     (GIT_COMMIT, GIT_COMMIT))

    retry_type = models.CharField(
        max_length=10,
        choices=RETRY_CHOICES,
        default=TIME
    )

    state_info = models.TextField(null=True)
    retry_count = models.PositiveSmallIntegerField(null=True)
    last_update_time = models.DateTimeField(null=True)
    arrival_time = models.DateTimeField(null=True)
    processing_start_time = models.DateTimeField(null=True)
    processing_end_time = models.DateTimeField(null=True)

    def __str__(self):
        """Convert to string."""
        return '{' + 'report_platform_id:{}, '\
            'host_inventory_api_version: {}, '\
            'source: {}, '\
            'source_metadata: {}, '\
            'account: {}, ' \
            'request_id: {}, '\
            'upload_ack_status: {}, ' \
            'upload_srv_kafka_msg: {}, ' \
            'git_commit: {}, '\
            'state: {}, '\
            'state_info: {}, '\
            'retry_count: {}, '\
            'retry_type: {}, '\
            'last_update_time: {}, '\
            'arrival_time: {}, '\
            'processing_start_time: {}, '\
            'processing_end_time: {} '.format(
                self.report_platform_id,
                self.host_inventory_api_version,
                self.source,
                self.source_metadata,
                self.account,
                self.request_id,
                self.upload_ack_status,
                self.upload_srv_kafka_msg,
                self.git_commit,
                self.state,
                self.state_info,
                self.retry_count,
                self.retry_type,
                self.last_update_time,
                self.arrival_time,
                self.processing_start_time,
                self.processing_end_time) + '}'

    class Meta:
        """Metadata for abstract report model."""

        abstract = True


class Report(AbstractReport):
    """Represents report records."""

    pass


class ReportArchive(AbstractReport):
    """Represents report archives."""

    pass
