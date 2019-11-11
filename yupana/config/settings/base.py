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

"""
Django settings for config project.

Generated by 'django-admin startproject' using Django 2.1.1.

For more information on this file, see
https://docs.djangoproject.com/en/2.1/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/2.1/ref/settings/
"""

import os

import environ

from boto3.session import Session
from botocore.exceptions import ClientError
from .env import ENVIRONMENT

ROOT_DIR = environ.Path(__file__) - 4
APPS_DIR = ROOT_DIR.path('yupana')

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/2.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = ENVIRONMENT.get_value('DJANGO_SECRET_KEY',
                                   default='base')
DEBUG = ENVIRONMENT.bool('DJANGO_DEBUG', default=False)
ALLOWED_HOSTS = ENVIRONMENT.get_value('DJANGO_ALLOWED_HOSTS', default=['*'])

# this is the time in minutes that we want to wait to retry a report
# default is 8 hours
RETRY_TIME = ENVIRONMENT.get_value('RETRY_TIME', default=480)

# this is the number of retries that we want to allow before failing a report
RETRIES_ALLOWED = ENVIRONMENT.get_value('RETRIES_ALLOWED', default=5)

# this is the max number of hosts per report slice
MAX_HOSTS_PER_REP = ENVIRONMENT.get_value('MAX_HOSTS_PER_REP', default=10000)

# this is how long we want to sleep in between looking for reports or slices
# to be processed
NEW_REPORT_QUERY_INTERVAL = ENVIRONMENT.get_value('NEW_REPORT_QUERY_INTERVAL', default=60)

# this is the count of futures to wait on results for at one time
HOSTS_UPLOAD_FUTURES_COUNT = ENVIRONMENT.get_value('HOSTS_UPLOAD_FUTURES_COUNT', default=100)

# this is the timeout for each kafka producer send
HOSTS_UPLOAD_TIMEOUT = ENVIRONMENT.get_value('HOSTS_UPLOAD_TIMEOUT', default=120)

# this is the retention limit of the report & slice archives (in seconds)
# the default is set to 4 weeks
ARCHIVE_RECORD_RETENTION_PERIOD = ENVIRONMENT.get_value(
    'ARCHIVE_RECORD_RETENTION_PERIOD', default=2419200)

# this is the interval at which garbage collection should run (in seconds)
# the default is set to 1 week
GARBAGE_COLLECTION_INTERVAL = ENVIRONMENT.get_value(
    'GARBAGE_COLLECTION_INTERVAL', default=604800)

# Logging
# https://docs.djangoproject.com/en/dev/topics/logging/
# https://docs.python.org/3.6/library/logging.html

# cloudwatch logging variables
CW_AWS_ACCESS_KEY_ID = ENVIRONMENT.get_value('CW_AWS_ACCESS_KEY_ID', default=None)
CW_AWS_SECRET_ACCESS_KEY = ENVIRONMENT.get_value('CW_AWS_SECRET_ACCESS_KEY', default=None)
CW_AWS_REGION = ENVIRONMENT.get_value('CW_AWS_REGION', default='us-east-1')
CW_LOG_GROUP = ENVIRONMENT.get_value('CW_LOG_GROUP', default='platform-dev')

LOGGING_LEVEL = os.getenv('DJANGO_LOG_LEVEL', 'INFO')
LOGGING_HANDLERS = os.getenv('DJANGO_LOG_HANDLERS', 'console').split(',')
LOGGING_FORMATTER = os.getenv('DJANGO_LOG_FORMATTER', 'simple')

if CW_AWS_ACCESS_KEY_ID:
    LOGGING_HANDLERS += ['watchtower']

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '%(asctime)s | %(levelname)s | '
                      '%(filename)s:%(funcName)s:%(lineno)d | %(message)s'
        },
        'simple': {
            'format': '[%(asctime)s] %(levelname)s: %(message)s'
        },
    },
    'handlers': {
        'console': {
            'level': ENVIRONMENT.get_value('DJANGO_CONSOLE_LOG_LEVEL', default='INFO'),
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        '': {
            'handlers': LOGGING_HANDLERS,
            'level': LOGGING_LEVEL,
        },
    },
}

if CW_AWS_ACCESS_KEY_ID:
    print('Configuring watchtower logging.')
    NAMESPACE = 'unknown'
    try:
        BOTO3_SESSION = Session(aws_access_key_id=CW_AWS_ACCESS_KEY_ID,
                                aws_secret_access_key=CW_AWS_SECRET_ACCESS_KEY,
                                region_name=CW_AWS_REGION)
        try:
            with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r") as f:
                NAMESPACE = f.read()
        except Exception:  # pylint: disable=W0703
            pass
        WATCHTOWER_HANDLER = {
            'level': LOGGING_LEVEL,
            'class': 'watchtower.CloudWatchLogHandler',
            'boto3_session': BOTO3_SESSION,
            'log_group': CW_LOG_GROUP,
            'stream_name': NAMESPACE,
            'formatter': LOGGING_FORMATTER,
        }
        LOGGING['handlers']['watchtower'] = WATCHTOWER_HANDLER
    except ClientError as cerr:
        print('CloudWatch logging setup failed: %s' % cerr)
else:
    print('Unable to configure watchtower logging.'
          'Please verify watchtower logging configuration!')

# Default apps go here
DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

# Any pip installed apps will go here
THIRD_PARTY_APPS = [
    'rest_framework',
    'django_prometheus',
]

# Apps specific to this project go here
LOCAL_APPS = [
    'api'
]


INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    'django_prometheus.middleware.PrometheusBeforeMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_prometheus.middleware.PrometheusAfterMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/2.1/ref/settings/#databases
ENGINES = {
    'sqlite': 'django.db.backends.sqlite3',
    'postgresql': 'django.db.backends.postgresql',
    'mysql': 'django.db.backends.mysql',
}

SERVICE_NAME = ENVIRONMENT.get_value('DATABASE_SERVICE_NAME',
                                     default='').upper().replace('-', '_')
if SERVICE_NAME:
    ENGINE = ENGINES.get(ENVIRONMENT.get_value('DATABASE_ENGINE'),
                         ENGINES['postgresql'])
else:
    ENGINE = ENGINES['sqlite']

NAME = ENVIRONMENT.get_value('DATABASE_NAME', default=None)

if not NAME and ENGINE == ENGINES['sqlite']:
    NAME = os.path.join(APPS_DIR, 'db.sqlite3')

DATABASES = {
    'ENGINE': ENGINE,
    'NAME': NAME,
    'USER': ENVIRONMENT.get_value('DATABASE_USER', default=None),
    'PASSWORD': ENVIRONMENT.get_value('DATABASE_PASSWORD', default=None),
    'HOST': ENVIRONMENT.get_value('{}_SERVICE_HOST'.format(SERVICE_NAME),
                                  default=None),
    'PORT': ENVIRONMENT.get_value('{}_SERVICE_PORT'.format(SERVICE_NAME),
                                  default=None),
}

# add ssl cert if specified
DATABASE_CERT = ENVIRONMENT.get_value('DATABASE_SERVICE_CERT', default=None)
if DATABASE_CERT:
    CERT_FILE = '/etc/ssl/certs/server.pem'
    DB_OPTIONS = {
        'OPTIONS': {
            'sslmode': 'verify-full',
            'sslrootcert': CERT_FILE
        }
    }
    DATABASES.update(DB_OPTIONS)

DATABASES = {
    'default': DATABASES
}

PROMETHEUS_EXPORT_MIGRATIONS = False

# Password validation
# https://docs.djangoproject.com/en/2.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/2.1/topics/i18n/

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_L10N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/2.1/howto/static-files/

STATIC_URL = ENVIRONMENT.get_value('DJANGO_STATIC_URL', default='/apidoc/')
STATIC_ROOT = ENVIRONMENT.get_value('DJANGO_STATIC_ROOT', default=str(APPS_DIR.path('staticfiles')))
STATICFILES_DIRS = [
    os.path.join(APPS_DIR, '..', 'apidoc'),
]

# Django Rest Framework
# http://www.django-rest-framework.org/api-guide/settings/

REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.LimitOffsetPagination',
    'PAGE_SIZE': 10,
}

# Override the initial ingest requirement to allow INITIAL_INGEST_NUM_MONTHS
# pylint: disable=simplifiable-if-expression
INGEST_OVERRIDE = False if os.getenv('INITIAL_INGEST_OVERRIDE', 'False') == 'False' else True

# Insights Kafka messaging address
INSIGHTS_KAFKA_HOST = os.getenv('INSIGHTS_KAFKA_HOST', 'localhost')

# Insights Kafka messaging address
INSIGHTS_KAFKA_PORT = os.getenv('INSIGHTS_KAFKA_PORT', '29092')

# Insights Kafka server address
INSIGHTS_KAFKA_ADDRESS = f'{INSIGHTS_KAFKA_HOST}:{INSIGHTS_KAFKA_PORT}'
