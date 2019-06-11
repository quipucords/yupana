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

"""Serializer for report progress."""

from rest_framework.serializers import (BooleanField,
                                        CharField,
                                        ChoiceField,
                                        DateTimeField,
                                        IntegerField,
                                        JSONField,
                                        ModelSerializer,
                                        UUIDField)

from api.models import Report, ReportArchive


class AbstractReportSerializer(ModelSerializer):
    """Abstract serializer for the Report models."""

    report_platform_id = UUIDField(format='hex_verbose', required=False)
    host_inventory_api_version = CharField(max_length=10, required=False)
    source = CharField(max_length=15, required=False)
    source_metadata = JSONField(allow_null=True, required=False)
    rh_account = CharField(max_length=50, required=False)
    upload_ack_status = CharField(max_length=10, required=False)
    upload_srv_kafka_msg = JSONField(required=True)
    git_commit = CharField(max_length=50, required=False)
    state = ChoiceField(choices=Report.STATE_CHOICES)
    retry_type = ChoiceField(choices=Report.RETRY_CHOICES, default=Report.TIME)
    state_info = JSONField(allow_null=True)
    retry_count = IntegerField(default=0)
    last_update_time = DateTimeField(allow_null=True)
    ready_to_archive = BooleanField(default=False)

    class Meta:
        """Meta class for ReportSerializer."""

        abstract = True
        fields = '__all__'


class ReportSerializer(AbstractReportSerializer):
    """Serializer for the Report model."""

    class Meta:
        """Meta class for the ReportSerializer."""

        model = Report
        fields = '__all__'


class ReportArchiveSerializer(AbstractReportSerializer):
    """Serializer for the ReportArchive model."""

    class Meta:
        """Meta class for the ReportArchiveSerializer."""

        model = ReportArchive
        fields = '__all__'
