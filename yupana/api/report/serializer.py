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

from rest_framework.serializers import (ChoiceField,
                                        DateField,
                                        IntegerField,
                                        JSONField,
                                        ModelSerializer,
                                        UUIDField)

from api.models import Report


class ReportSerializer(ModelSerializer):
    """Serializer for the Report model."""

    report_platform_id = UUIDField(format='hex_verbose',
                                   read_only=True)
    upload_srv_kafka_msg = JSONField(read_only=True)
    report_json = JSONField(read_only=True)
    state = ChoiceField(read_only=True, choices=Report.STATE_CHOICES)
    stage_info = JSONField(read_only=True)
    retry_count = IntegerField(null=True)
    last_update_time = DateField(null=True)
    failed_hosts = JSONField(read_only=True)
    candidate_hosts = JSONField(read_only=True)

    class Meta:
        """Meta class for ReportSerializer."""

        model = Report
        fields = '__all__'
