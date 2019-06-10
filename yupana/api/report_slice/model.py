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

"""Model for report slice progress."""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from api.report.model import Report, ReportArchive


class AbstractReportSlice(models.Model):
    """Represents report slice information."""

    report_platform_id = models.UUIDField(null=True)
    report_slice_id = models.UUIDField(null=True)
    rh_account = models.CharField(max_length=50, null=True)
    report_json = models.TextField(null=True)
    git_commit = models.CharField(max_length=50, null=True)
    ready_to_archive = models.BooleanField(null=False, default=False)

    PENDING = 'pending'
    NEW = 'new'
    RETRY_VALIDATION = 'retry_validation'
    FAILED_VALIDATION = 'failed_validation'
    VALIDATED = 'validated'
    STARTED = 'started'
    HOSTS_UPLOADED = 'hosts uploaded'
    FAILED_HOSTS_UPLOAD = 'failed to upload hosts'
    STATE_CHOICES = ((PENDING, PENDING),
                     (NEW, NEW),
                     (RETRY_VALIDATION, RETRY_VALIDATION),
                     (FAILED_VALIDATION, FAILED_VALIDATION),
                     (VALIDATED, VALIDATED),
                     (STARTED, STARTED),
                     (HOSTS_UPLOADED, HOSTS_UPLOADED),
                     (FAILED_HOSTS_UPLOAD, FAILED_HOSTS_UPLOAD))

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
    failed_hosts = models.TextField(null=True)
    candidate_hosts = models.TextField(null=True)
    hosts_count = models.PositiveSmallIntegerField(
        null=False, validators=[MinValueValidator(1), MaxValueValidator(10000)])

    def __str__(self):
        """Convert to string."""
        return '{' + 'report_platform_id:{}, '\
            'report_slice_id: {}, '\
            'rh_account: {}, ' \
            'report_json: {}, '\
            'git_commit: {}, '\
            'state: {}, '\
            'state_info: {}, '\
            'retry_count: {}, '\
            'retry_type: {}, '\
            'last_update_time: {}, '\
            'failed_hosts: {}, '\
            'candidate_hosts: {} '\
            'hosts_count: {}'.format(
                self.report_platform_id,
                self.report_slice_id,
                self.rh_account,
                self.report_json,
                self.git_commit,
                self.state,
                self.state_info,
                self.retry_count,
                self.retry_type,
                self.last_update_time,
                self.failed_hosts,
                self.candidate_hosts,
                self.hosts_count) + '}'

    class Meta:
        """Metadata for abstract report slice model."""

        abstract = True


class ReportSlice(AbstractReportSlice):  # pylint: disable=too-many-instance-attributes
    """Represents report slice records."""

    report = models.ForeignKey(Report, null=True, on_delete=models.CASCADE)


class ReportSliceArchive(AbstractReportSlice):
    """Represents report slice archives."""

    report = models.ForeignKey(ReportArchive, null=True, on_delete=models.CASCADE)
