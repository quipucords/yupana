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

"""Serializer for forensics."""

from rest_framework.serializers import (CharField,
                                        ChoiceField,
                                        JSONField,
                                        ModelSerializer,
                                        UUIDField)

from api.models import InventoryUploadError


class InventoryUploadErrorSerializer(ModelSerializer):
    """Serializer for the InventoryUploadError model."""

    report_platform_id = UUIDField(format='hex_verbose', required=False)
    report_slice_id = UUIDField(format='hex_verbose', required=False)
    account = CharField(max_length=50, required=True)
    source = ChoiceField(choices=InventoryUploadError.SOURCE_CHOICES, required=False)
    details = JSONField(allow_null=False)

    class Meta:
        """Meta class for InventoryUploadErrorSerializer."""

        model = InventoryUploadError
        fields = '__all__'
