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
from processor.report_consumer import format_message

from api.models import ReportArchive
from config.settings.base import (ARCHIVE_RECORD_RETENTION_PERIOD,
                                  GARBAGE_COLLECTION_INTERVAL)

LOG = logging.getLogger(__name__)
GARBAGE_COLLECTION_LOOP = asyncio.new_event_loop()
# this is how often we want garbage collection to run
# (set to weeks & we convert to seconds below for asyncio)
GARBAGE_COLLECTION_INTERVAL_SECONDS = int(GARBAGE_COLLECTION_INTERVAL) * 604800
# this is the number of weeks that we want to delete objects after
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
                    'Will check again for outdated archives in %s week(s).'
                    % GARBAGE_COLLECTION_INTERVAL))
            await asyncio.sleep(GARBAGE_COLLECTION_INTERVAL_SECONDS)

    def remove_outdated_archives(self):
        """Query for archived reports and delete them if they have come of age."""
        current_time = datetime.now(pytz.utc)
        created_time_limit = current_time - timedelta(weeks=ARCHIVE_RECORD_RETENTION_PERIOD)
        garbage_collection_query = ReportArchive.objects.filter(
            processing_end_time__lte=created_time_limit)
        # we only have to delete the archived reports because deleting an archived report deletes
        # all of the associated archived report slices
        if garbage_collection_query:
            for archived_report in garbage_collection_query:
                LOG.info(
                    format_message(
                        self.prefix,
                        'Deleting archived report with id: %s' % archived_report.id))
                try:
                    ReportArchive.objects.get(id=archived_report.id).delete()
                except ReportArchive.DoesNotExist:
                    pass
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
