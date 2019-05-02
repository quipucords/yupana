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
                                        DateField,
                                        IntegerField,
                                        JSONField,
                                        ModelSerializer)

from api.models import AbstractReport


class ReportSerializer(ModelSerializer):
    """Serializer for the Report model."""

    report_platform_id = CharField(required=False)
    report_version = CharField(required=False)
    qpc_server_version = CharField(required=False)
    qpc_server_id = CharField(required=False)
    rh_account = CharField(required=False)
    upload_ack_status = CharField(required=False)
    upload_srv_kafka_msg = JSONField(read_only=True)
    git_commit = CharField(required=False)
    state = ChoiceField(read_only=True, choices=AbstractReport.STATE_CHOICES)
    retry_type = ChoiceField(read_only=True, choices=AbstractReport.RETRY_CHOICES)
    state_info = JSONField(null=True)
    retry_count = IntegerField(null=True)
    last_update_time = DateField(null=True)
    ready_to_archive = BooleanField(required=True)

    class Meta:
        """Meta class for ReportSerializer."""

        model = AbstractReport
        fields = '__all__'
