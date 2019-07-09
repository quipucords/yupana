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

"""Serializer for legacy report progress."""

from rest_framework.serializers import (BooleanField,
                                        CharField,
                                        ChoiceField,
                                        DateTimeField,
                                        IntegerField,
                                        JSONField,
                                        ModelSerializer,
                                        UUIDField)

from api.models import LegacyReport, LegacyReportArchive


class LegacyAbstractReportSerializer(ModelSerializer):
    """Abstract serializer for the LegacyReport models."""

    report_platform_id = UUIDField(format='hex_verbose', required=False)
    host_inventory_api_version = CharField(max_length=10, required=False)
    source = CharField(max_length=15, required=False)
    source_metadata = JSONField(allow_null=True, required=False)
    account = CharField(max_length=50, required=False)
    request_id = CharField(max_length=50, required=False)
    upload_ack_status = CharField(max_length=10, required=False)
    upload_srv_kafka_msg = JSONField(required=True)
    git_commit = CharField(max_length=50, required=False)
    state = ChoiceField(choices=LegacyReport.STATE_CHOICES)
    retry_type = ChoiceField(choices=LegacyReport.RETRY_CHOICES, default=LegacyReport.TIME)
    state_info = JSONField(allow_null=True)
    retry_count = IntegerField(default=0)
    last_update_time = DateTimeField(allow_null=True)
    ready_to_archive = BooleanField(default=False)
    arrival_time = DateTimeField(allow_null=False)
    processing_start_time = DateTimeField(allow_null=True, required=False)
    processing_end_time = DateTimeField(allow_null=True, required=False)

    class Meta:
        """Meta class for LegacyReportSerializer."""

        abstract = True
        fields = '__all__'


class LegacyReportSerializer(LegacyAbstractReportSerializer):
    """Serializer for the LegacyReport model."""

    class Meta:
        """Meta class for the LegacyReportSerializer."""

        model = LegacyReport
        fields = '__all__'


class LegacyReportArchiveSerializer(LegacyAbstractReportSerializer):
    """Serializer for the LegacyReportArchive model."""

    class Meta:
        """Meta class for the LegacyReportArchiveSerializer."""

        model = LegacyReportArchive
        fields = '__all__'
