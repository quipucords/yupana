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
"""Test the status API."""

import logging
from collections import namedtuple
from unittest.mock import ANY, Mock, patch

from django.test import TestCase

from api.status.model import Status


class StatusModelTest(TestCase):
    """Tests against the status functions."""

    @classmethod
    def setUpClass(cls):
        """Test Class setup."""
        # remove filters on logging
        logging.disable(logging.NOTSET)
        cls.status_info = Status()

    @classmethod
    def tearDownClass(cls):
        """Test Class teardown."""
        # restore filters on logging
        logging.disable(logging.CRITICAL)

    @patch('os.environ')
    def test_commit_with_env(self, mock_os):
        """Test the commit method via environment."""
        expected = 'buildnum'
        mock_os.get.return_value = expected
        result = self.status_info.git_commit
        self.assertEqual(result, expected)

    @patch('os.environ')
    def test_env_vars(self, mock_envs):
        """Test the environment variables."""
        expected = {
            'key': 'value',
            'password': '1234',
            'good_PassWord': '2345'}
        mock_envs.items.return_value = expected.items()
        result = self.status_info.environment_vars

        self.assertNotEqual(result, expected)
        expected['password'] = '*' * 8
        expected['good_PassWord'] = '*' * 8
        self.assertEqual(result, expected)

    @patch('subprocess.run')
    @patch('api.status.model.os.environ')
    def test_commit_with_subprocess(self, mock_os, mock_subprocess):
        """Test the commit method via subprocess."""
        expected = 'buildnum'
        run = Mock()
        run.stdout = b'buildnum'
        mock_subprocess.return_value = run
        mock_os.get.return_value = None
        result = self.status_info.git_commit
        self.assertEqual(result, expected)

    @patch('platform.uname')
    def test_platform_info(self, mock_platform):
        """Test the platform_info method."""
        platform_record = namedtuple('Platform', ['os', 'version'])
        a_plat = platform_record('Red Hat', '7.4')
        mock_platform.return_value = a_plat
        result = self.status_info.platform_info
        self.assertEqual(result['os'], 'Red Hat')
        self.assertEqual(result['version'], '7.4')

    @patch('sys.version')
    def test_python_version(self, mock_sys_ver):
        """Test the python_version method."""
        expected = 'Python 3.6'
        mock_sys_ver.replace.return_value = expected
        result = self.status_info.python_version
        self.assertEqual(result, expected)

    @patch('sys.modules')
    def test_modules(self, mock_modules):
        """Test the modules method."""
        expected = {'module1': 'version1',
                    'module2': 'version2'}
        mod1 = Mock(__version__='version1')
        mod2 = Mock(__version__='version2')
        mock_modules.items.return_value = (('module1', mod1),
                                           ('module2', mod2))
        result = self.status_info.modules
        self.assertEqual(result, expected)

    @patch('api.status.model.logger.info')
    def test_startup_with_modules(self, mock_logger):  # pylint: disable=no-self-use
        """Test the startup method with a module list."""
        self.status_info.startup()
        mock_logger.assert_called_with(ANY, ANY, ANY)
