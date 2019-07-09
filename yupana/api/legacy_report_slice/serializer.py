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

"""Serializer for legacy report slice progress."""

from rest_framework.serializers import (BooleanField,
                                        CharField,
                                        ChoiceField,
                                        DateTimeField,
                                        IntegerField,
                                        JSONField,
                                        ModelSerializer,
                                        UUIDField)

from api.models import LegacyReportSlice, LegacyReportSliceArchive
from config.settings.base import MAX_HOSTS_PER_REP

MAX_HOSTS_PER_REP = int(MAX_HOSTS_PER_REP)


class LegacyAbstractReportSliceSerializer(ModelSerializer):
    """Abstract serializer for the LegacyReportSlice models."""

    report_platform_id = UUIDField(format='hex_verbose', required=False)
    report_slice_id = UUIDField(format='hex_verbose', required=False)
    account = CharField(max_length=50, required=False)
    report_json = JSONField(allow_null=False)
    git_commit = CharField(max_length=50, required=False)
    source = CharField(max_length=15, required=True)
    state = ChoiceField(choices=LegacyReportSlice.STATE_CHOICES)
    retry_type = ChoiceField(choices=LegacyReportSlice.RETRY_CHOICES, default=LegacyReportSlice.TIME)
    state_info = JSONField(allow_null=False)
    retry_count = IntegerField(default=0)
    hosts_count = IntegerField(min_value=1, max_value=MAX_HOSTS_PER_REP)
    last_update_time = DateTimeField(allow_null=False)
    failed_hosts = JSONField(allow_null=True)
    candidate_hosts = JSONField(allow_null=True)
    ready_to_archive = BooleanField(default=False)
    creation_time = DateTimeField(allow_null=False)
    processing_start_time = DateTimeField(allow_null=True, required=False)
    processing_end_time = DateTimeField(allow_null=True, required=False)

    class Meta:
        """Meta class for LegacyAbstractReportSliceSerializer."""

        abstract = True
        fields = '__all__'


class LegacyReportSliceSerializer(LegacyAbstractReportSliceSerializer):
    """Serializer for the LegacyReportSlice Model."""

    class Meta:
        """Meta class for the LegacyReportSliceSerializer."""

        model = LegacyReportSlice
        fields = '__all__'


class LegacyReportSliceArchiveSerializer(LegacyAbstractReportSliceSerializer):
    """Serializer for the LegacyReportSliceArchive Model."""

    class Meta:
        """Meta class for the LegacyReportSliceArchiveSerializer."""

        model = LegacyReportSliceArchive
        fields = '__all__'
