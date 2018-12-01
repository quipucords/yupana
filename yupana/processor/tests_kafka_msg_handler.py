#
# Copyright 2018 Red Hat, Inc.
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
"""Tests kafka message handler."""

import json

import processor.kafka_msg_handler as msg_handler
from django.test import TestCase


class KafkaMsg:
    """Create a kafka msg."""

    def __init__(self, topic, url):
        """Initialize the message."""
        self.topic = topic
        value_dict = {'url': url}
        value_str = json.dumps(value_dict)
        self.value = value_str.encode('utf-8')


class KafkaMsgHandlerTest(TestCase):
    """Test Cases for the Kafka msg handler."""

    def test_verify_report_success(self):
        """Test to verify a QPC report with the correct structure passes validation."""
        report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'system_fingerprints': [{'key': 'value'}]}

        status = msg_handler.verify_report_details(report_json)
        self.assertEqual(status, msg_handler.SUCCESS_CONFIRM_STATUS)

    def test_verify_report_missing_id(self):
        """Test to verify a QPC report with a missing id is failed."""
        report_json = {
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'system_fingerprints': [{'key': 'value'}]}

        status = msg_handler.verify_report_details(report_json)
        self.assertEqual(status, msg_handler.FAILURE_CONFIRM_STATUS)

    def test_verify_report_invalid_report_type(self):
        """Test to verify a QPC report with an invalid report_type is failed."""
        report_json = {
            'report_id': 1,
            'report_type': 'details',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'system_fingerprints': [{'key': 'value'}]}

        status = msg_handler.verify_report_details(report_json)
        self.assertEqual(status, msg_handler.FAILURE_CONFIRM_STATUS)

    def test_verify_report_missing_version(self):
        """Test to verify a QPC report missing report_version is failed."""
        report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'system_fingerprints': [{'key': 'value'}]}

        status = msg_handler.verify_report_details(report_json)
        self.assertEqual(status, msg_handler.FAILURE_CONFIRM_STATUS)

    def test_verify_report_missing_platform_id(self):
        """Test to verify a QPC report missing report_platform_id is failed."""
        report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'system_fingerprints': [{'key': 'value'}]}

        status = msg_handler.verify_report_details(report_json)
        self.assertEqual(status, msg_handler.FAILURE_CONFIRM_STATUS)

    def test_verify_report_missing_fingerprints(self):
        """Test to verify a QPC report with empty fingerprints is failed."""
        report_json = {
            'report_id': 1,
            'report_type': 'deployments',
            'report_version': '1.0.0.1b025b8',
            'status': 'completed',
            'report_platform_id': '5f2cc1fd-ec66-4c67-be1b-171a595ce319',
            'system_fingerprints': []}

        status = msg_handler.verify_report_details(report_json)
        self.assertEqual(status, msg_handler.FAILURE_CONFIRM_STATUS)
