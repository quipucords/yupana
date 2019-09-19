#
# Copyright 2019 Red Hat, Inc.
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
"""Garbage Collection loop."""

import asyncio
import logging
import threading
from datetime import datetime, timedelta

import pytz
from processor.report_consumer import DB_ERRORS, format_message

from api.models import ReportArchive
from config.settings.base import (ARCHIVE_RECORD_RETENTION_PERIOD,
                                  GARBAGE_COLLECTION_INTERVAL)

LOG = logging.getLogger(__name__)
GARBAGE_COLLECTION_LOOP = asyncio.new_event_loop()
# this is how often we want garbage collection to run
# (set to seconds) - default value is 1 week
GARBAGE_COLLECTION_INTERVAL = int(GARBAGE_COLLECTION_INTERVAL)
# this is the period in seconds for which we keep archives - default value is 4 weeks
ARCHIVE_RECORD_RETENTION_PERIOD = int(ARCHIVE_RECORD_RETENTION_PERIOD)


class GarbageCollector():
    """Class for deleting archived reports & associated slices."""

    def __init__(self):
        """Initialize a garbage collector."""
        self.should_run = True
        self.prefix = 'GARBAGE COLLECTING'

    async def run(self):
        """Run the garbage collector in a loop.

        Later, if we find that we want to stop looping, we can
        manipulate the class variable should_run.
        """
        while self.should_run:
            self.remove_outdated_archives()
            LOG.info(
                format_message(
                    self.prefix,
                    'Going to sleep. '
                    'Will check again for outdated archives in %s seconds.'
                    % int(GARBAGE_COLLECTION_INTERVAL)))
            await asyncio.sleep(GARBAGE_COLLECTION_INTERVAL)

    @DB_ERRORS.count_exceptions()
    def remove_outdated_archives(self):
        """Query for archived reports and delete them if they have come of age."""
        current_time = datetime.now(pytz.utc)
        created_time_limit = current_time - timedelta(seconds=ARCHIVE_RECORD_RETENTION_PERIOD)
        # we only have to delete the archived reports because deleting an archived report deletes
        # all of the associated archived report slices
        outdated_report_archives = ReportArchive.objects.filter(
            processing_end_time__lte=created_time_limit)
        if outdated_report_archives:
            _, deleted_info = outdated_report_archives.delete()
            report_total = deleted_info.get('api.ReportArchive')
            report_slice_total = deleted_info.get('api.ReportSliceArchive')
            LOG.info(format_message(
                self.prefix,
                'Deleted %s archived report(s) & '
                '%s archived report slice(s) older than %s seconds.' %
                (report_total, report_slice_total, int(ARCHIVE_RECORD_RETENTION_PERIOD))))
        else:
            LOG.info(
                format_message(
                    self.prefix,
                    'No archived reports to delete.'
                )
            )


def asyncio_garbage_collection_thread(loop):  # pragma: no cover
    """
    Worker thread function to run the asyncio event loop.

    Creates a garbage collector and calls the run method.

    :param loop: event loop
    :returns None
    """
    collector = GarbageCollector()
    loop.run_until_complete(collector.run())


def initialize_garbage_collection_loop():  # pragma: no cover
    """
    Create asyncio tasks and daemon thread to run event loop.

    Calls the garbage collection thread.

    :param None
    :returns None
    """
    event_loop_thread = threading.Thread(target=asyncio_garbage_collection_thread,
                                         args=(GARBAGE_COLLECTION_LOOP,))
    event_loop_thread.daemon = True
    event_loop_thread.start()
