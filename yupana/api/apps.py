#
# Copyright 2018-2019 Red Hat, Inc.
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
"""API application configuration module."""

import logging
import sys

from django.apps import AppConfig
from django.db.utils import OperationalError, ProgrammingError

from config.settings.env import ENVIRONMENT


logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


class ApiConfig(AppConfig):
    """API application configuration."""

    name = 'api'

    def ready(self):
        """Determine if app is ready on application startup."""
        # Don't run on Django tab completion commands
        if 'manage.py' in sys.argv[0] and 'runserver' not in sys.argv:
            return
        try:
            self.startup_status()
            self.check_and_create_service_admin()
            self.start_upload_report_consumer()
            self.start_report_processor()
            self.start_report_slice_processor()
            self.start_garbage_collection()
        except (OperationalError, ProgrammingError) as op_error:
            if 'no such table' in str(op_error) or \
                    'does not exist' in str(op_error):
                # skip this if we haven't created tables yet.
                return
            logger.error('Error: %s.', op_error)

    def startup_status(self):  # pylint: disable=R0201
        """Log the status of the server at startup."""
        # noqa: E402 pylint: disable=C0413
        from api.status.model import Status
        status_info = Status()

        status_info.startup()

    def create_service_admin(self, service_email):  # pylint: disable=R0201
        """Create the Service Admin."""
        # noqa: E402 pylint: disable=C0413
        from django.contrib.auth.models import User
        service_user = ENVIRONMENT.get_value('SERVICE_ADMIN_USER',
                                             default='admin')
        service_pass = ENVIRONMENT.get_value('SERVICE_ADMIN_PASSWORD',
                                             default='pass')

        User.objects.create_superuser(service_user,
                                      service_email,
                                      service_pass)
        logger.info('Created Service Admin: %s.', service_email)

    @staticmethod
    def start_upload_report_consumer():
        """Start the kafka consumer for incoming reports."""
        pause_kafka_for_file_upload = ENVIRONMENT.get_value(
            'PAUSE_KAFKA_FOR_FILE_UPLOAD_SERVICE', default=False)
        host_inventory_upload_mode = ENVIRONMENT.get_value(
            'HOST_INVENTORY_UPLOAD_MODE', default='http')
        if not pause_kafka_for_file_upload:
            if host_inventory_upload_mode == 'http':
                from processor.legacy_report_consumer import initialize_kafka_handler
                logger.info('Initializing the legacy kafka report consumer.')
                initialize_kafka_handler()
            else:
                from processor.report_consumer import initialize_upload_report_consumer
                logger.info('Initializing the kafka report consumer.')
                initialize_upload_report_consumer()
        else:
            logger.info('Kafka report consumer paused for file upload service.')

    @staticmethod
    def start_report_processor():
        """Start the report processor."""
        host_inventory_upload_mode = ENVIRONMENT.get_value(
            'HOST_INVENTORY_UPLOAD_MODE', default='http')
        if host_inventory_upload_mode == 'http':
            from processor.legacy_report_processor import initialize_report_processor
            logger.info('Initializing the legacy report processor.')
            initialize_report_processor()
        else:
            from processor.report_processor import initialize_report_processor
            logger.info('Initializing the report processor.')
            initialize_report_processor()

    @staticmethod
    def start_report_slice_processor():
        """Start the report slice processor."""
        host_inventory_upload_mode = ENVIRONMENT.get_value(
            'HOST_INVENTORY_UPLOAD_MODE', default='http')
        if host_inventory_upload_mode == 'http':
            from processor.legacy_report_slice_processor import initialize_report_slice_processor
            logger.info('Initializing the legacy report slice processor.')
            initialize_report_slice_processor()
        else:
            from processor.report_slice_processor import initialize_report_slice_processor
            logger.info('Initializing the report slice processor.')
            initialize_report_slice_processor()

    @staticmethod
    def start_garbage_collection():
        """Start the garbage collector loop."""
        from processor.garbage_collection import initialize_garbage_collection_loop
        logger.info('Initializing the garbage collector.')
        initialize_garbage_collection_loop()

    def check_and_create_service_admin(self):  # pylint: disable=R0201
        """Check for the service admin and create it if necessary."""
        # noqa: E402 pylint: disable=C0413
        from django.contrib.auth.models import User
        service_email = ENVIRONMENT.get_value('SERVICE_ADMIN_EMAIL',
                                              default='admin@example.com')
        admin_not_present = User.objects.filter(
            email=service_email).count() == 0
        if admin_not_present:
            self.create_service_admin(service_email)
        else:
            logger.info('Service Admin: %s.', service_email)
