#
# Copyright 2018-2019 Red Hat, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

"""View for server status."""
import logging

from processor.processor_utils import (format_message,
                                       list_name_of_active_threads,
                                       list_name_of_processors)
from rest_framework import permissions, status as http_status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from api.status.model import Status
from api.status.serializer import StatusSerializer
from prometheus_client import Counter

LOG = logging.getLogger(__name__)
PROCESSOR_DEAD_EXCEPTION = Counter('yupana_processor_dead',
                        'Total number of time yupana process thread dies')


@api_view(['GET', 'HEAD'])
@permission_classes((permissions.AllowAny,))
def status(request):
    """Provide the server status information.

    @api {get} /api/subscriptions/v1/status/ Request server status
    @apiName GetStatus
    @apiGroup Status
    @apiVersion 1.0.0
    @apiDescription Request server status.

    @apiSuccess {Number} api_version The version of the API.
    @apiSuccess {String} release_version The release version.
    @apiSuccess {String} commit  The commit hash of the code base.
    @apiSuccess {Object} modules  The code modules found on the server.
    @apiSuccess {Object} platform_info  The server platform information.
    @apiSuccess {String} python_version  The version of python on the server.
    @apiSuccess {String} server_address  The address of the server.
    @apiSuccessExample {json} Success-Response:
        HTTP/1.1 200 OK
        {
            "api_version": 1,
            "release_version": "1.0.0.178d2ea",
            "commit": "178d2ea",
            "server_address": "127.0.0.1:8001",
            "platform_info": {
                "system": "Darwin",
                "node": "node-1.example.com",
                "release": "17.5.0",
                "version": "Darwin Kernel Version 17.5.0",
                "machine": "x86_64",
                "processor": "i386"
                },
            "python_version": "3.6.1",
            "modules": {
                "coverage": "4.5.1",
                "coverage.version": "4.5.1",
                "coverage.xmlreport": "4.5.1",
                "cryptography": "2.0.3",
                "ctypes": "1.1.0",
                "ctypes.macholib": "1.0",
                "decimal": "1.70",
                "django": "1.11.5",
                "django.utils.six": "1.10.0",
                "django_filters": "1.0.4",
                "http.server": "0.6"
                }
        }
    """
    status_info = Status()
    serializer = StatusSerializer(status_info)
    server_info = serializer.data
    server_info['server_address'] = request.META.get('HTTP_HOST', 'localhost')
    total_processors_names = list_name_of_processors()
    active_threads_names = list_name_of_active_threads()
    if not all(item in active_threads_names for item in total_processors_names):
        dead_processors = set(total_processors_names).difference(active_threads_names)
        PROCESSOR_DEAD_EXCEPTION.inc()
        LOG.error(format_message('SERVICE STATUS', 'Dead processors - %s' % dead_processors))
        return Response('ERROR: Processor thread exited',
                        status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(server_info)
