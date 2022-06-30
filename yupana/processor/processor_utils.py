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
"""Utilities for all of the processor classes."""
import asyncio
import logging
import threading
from time import sleep

from config.settings.base import (SLEEP_PERIOD_WHEN_EVENT_LOOP_ERROR)

SLEEP_PERIOD_WHEN_EVENT_LOOP_ERROR = int(SLEEP_PERIOD_WHEN_EVENT_LOOP_ERROR)

LOG = logging.getLogger(__name__)
UPLOAD_REPORT_CONSUMER_LOOP = asyncio.get_event_loop()
REPORT_PROCESSING_LOOP = asyncio.new_event_loop()
SLICE_PROCESSING_LOOP = asyncio.new_event_loop()
GARBAGE_COLLECTION_LOOP = asyncio.new_event_loop()
PROCESSOR_INSTANCES = []  # this list holds processor instances that have kafka components


def list_name_of_active_threads():
    """List of names of active thread."""
    return list(map(lambda i: i.name, threading.enumerate()))


def list_name_of_processors():
    """List of processor."""
    return list(map(lambda i: i.processor_name, PROCESSOR_INSTANCES))


def format_message(prefix, message, account_number=None, org_id=None,
                   report_platform_id=None):
    """Format log messages in a consistent way.

    :param prefix: (str) A meaningful prefix to be displayed in all caps.
    :param message: (str) A short message describing the state
    :param account_number: (str) The account sending the report.
    :param report_platform_id: (str) The qpc report id.
    :returns: (str) containing formatted message
    """
    if not report_platform_id and not account_number and not org_id:
        actual_message = 'Report %s - %s' % (prefix, message)
    elif account_number and not report_platform_id:
        actual_message = 'Report(account=%s, org_id=%s) %s - %s' % (
            account_number,
            org_id,
            prefix,
            message)
    else:
        actual_message = 'Report(account=%s, org_id=%s, report_platform_id=%s) %s - %s' % (
            account_number,
            org_id,
            report_platform_id, prefix,
            message)

    return actual_message


def print_error_loop_event():
    """Print & wait for seconds when event loop error occurred."""
    prefix = 'EVENT LOOP ERROR WHILE PROCESSING'
    LOG.warning(format_message(prefix, 'Wait for %s seconds.' % SLEEP_PERIOD_WHEN_EVENT_LOOP_ERROR))
    sleep(SLEEP_PERIOD_WHEN_EVENT_LOOP_ERROR)
