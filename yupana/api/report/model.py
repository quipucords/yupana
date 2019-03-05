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

    report_platform_id = models.CharField(max_length=50, null=True)
    rh_account = models.TextField(null=True)
    upload_ack_status = models.TextField(null=True)
    upload_srv_kafka_msg = models.TextField(null=True)
    report_json = models.TextField(null=True)
    commit_info = models.TextField(null=True)

    NEW = 'new'
    STARTED = 'started'
    DOWNLOADED = 'downloaded'
    FAILED_DOWNLOAD = 'failed to download'
    VALIDATED = 'validated'
    FAILED_VALIDATION = 'failed validation'
    VALIDATION_REPORTED = 'validation reported'
    FAILED_VALIDATION_REPORTING = 'failed to report validation'
    HOSTS_UPLOADED = 'hosts uploaded'
    FAILED_HOSTS_UPLOAD = 'failed to upload hosts'
    STATE_CHOICES = (('NEW', NEW),
                     ('STARTED', STARTED),
                     ('DOWNLOADED', DOWNLOADED),
                     ('FAILED_DOWNLOAD', FAILED_DOWNLOAD),
                     ('VALIDATED', VALIDATED),
                     ('FAILED_VALIDATION', FAILED_VALIDATION),
                     ('VALIDATION_REPORTED', VALIDATION_REPORTED),
                     ('FAILED_VALIDATION_REPORTING', FAILED_VALIDATION_REPORTING),
                     ('HOSTS_UPLOADED', HOSTS_UPLOADED),
                     ('FAILED_HOSTS_UPLOAD', FAILED_HOSTS_UPLOAD))

    state = models.CharField(
        max_length=28,
        choices=STATE_CHOICES,
        default=NEW
    )

    TIME = 'time'
    COMMIT = 'commit'
    RETRY_CHOICES = (('TIME', TIME),
                     ('COMMIT', COMMIT))

    retry_type = models.CharField(
        max_length=10,
        choices=RETRY_CHOICES,
        default=TIME
    )

    state_info = models.TextField(null=True)
    retry_count = models.PositiveSmallIntegerField(null=True)
    last_update_time = models.DateTimeField(null=True)
    failed_hosts = models.TextField(null=True)
    candidate_hosts = models.TextField(null=True)

    def __str__(self):
        """Convert to string."""
        return '{' + 'report_platform_id:{}, '\
            'rh_account: {}, ' \
            'upload_ack_status: {}, ' \
            'upload_srv_kafka_msg:{}, ' \
            'report_json:{}, '\
            'commit_info:{}, '\
            'state:{}, '\
            'state_info:{}, '\
            'retry_count:{}, '\
            'retry_type:{}, '\
            'last_update_time:{}, '\
            'failed_hosts:{}, '\
            'candidate_hosts:{} '.format(self.report_platform_id,
                                         self.rh_account,
                                         self.upload_ack_status,
                                         self.upload_srv_kafka_msg,
                                         self.report_json,
                                         self.commit_info,
                                         self.state,
                                         self.state_info,
                                         self.retry_count,
                                         self.retry_type,
                                         self.last_update_time,
                                         self.failed_hosts,
                                         self.candidate_hosts) + '}'

    class Meta:
        """Metadata for abstract report model."""

        abstract = True


class Report(AbstractReport):
    """Represents report records."""

    pass


class ReportArchive(AbstractReport):
    """Represents report archives."""

    pass
