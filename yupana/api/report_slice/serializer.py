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

"""Serializer for report slice progress."""

from rest_framework.serializers import (BooleanField,
                                        CharField,
                                        ChoiceField,
                                        DateField,
                                        IntegerField,
                                        JSONField,
                                        ModelSerializer)

from api.models import AbstractReportSlice


class ReportSliceSerializer(ModelSerializer):
    """Serializer for the ReportSlice model."""

    report_platform_id = CharField(required=False)
    rh_account = CharField(required=False)
    report_json = JSONField(null=True)
    git_commit = CharField(required=False)
    state = ChoiceField(read_only=True, choices=AbstractReportSlice.STATE_CHOICES)
    retry_type = ChoiceField(read_only=True, choices=AbstractReportSlice.RETRY_CHOICES)
    state_info = JSONField(null=True)
    retry_count = IntegerField(null=True)
    last_update_time = DateField(null=True)
    failed_hosts = JSONField(null=True)
    candidate_hosts = JSONField(null=True)
    ready_to_archive = BooleanField(required=True)

    class Meta:
        """Meta class for ReportSliceSerializer."""

        model = AbstractReportSlice
        fields = '__all__'
