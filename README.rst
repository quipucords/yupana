=============
Yupana README
=============

|license| |Build Status| |codecov| |Updates| |Python 3| |Docs|

~~~~~
About
~~~~~


Getting Started
===============

This is a Python project developed using Python 3.6. Make sure you have at least this version installed.

Development
===========

To get started developing against Yupana first clone a local copy of the git repository. ::

    git clone https://github.com/quipucords/yupana

Developing inside a virtual environment is recommended. A Pipfile is provided. Pipenv is recommended for combining virtual environment (virtualenv) and dependency management (pip). To install pipenv, use pip ::

    pip3 install pipenv

Then project dependencies and a virtual environment can be created using ::

    pipenv install --dev

To activate the virtual environment run ::

    pipenv shell

Configuration
^^^^^^^^^^^^^

This project is developed using the Django web framework. Many configuration settings can be read in from a `.env` file. An example file `.env.example` is provided in the repository. To use the defaults simply ::

    cp .env.example .env


Modify as you see fit.


Server
^^^^^^

To run a local dev Django server you can use ::

    make server-init
    make serve

Run Server with gunicorn
^^^^^^^^^^^^^^^^^^^^^^^^

To run a local gunicorn server with yupana do the following::

    make server-init
    gunicorn config.wsgi -c ./yupana/config/gunicorn.py --chdir=./yupana/

Testing and Linting
-------------------

Yupana uses tox to standardize the environment used when running tests. Essentially, tox manages its own virtual environment and a copy of required dependencies to run tests. To ensure a clean tox environment run ::

    tox -r

This will rebuild the tox virtual env and then run all tests.

To run unit tests specifically::

    tox -e py36

To lint the code base ::

    tox -e lint


.. |license| image:: https://img.shields.io/github/license/quipucords/yupana.svg
.. |Updates| image:: https://pyup.io/repos/github/quipucords/yupana/shield.svg
   :target: https://pyup.io/repos/github/quipucords/yupana/
.. |Python 3| image:: https://pyup.io/repos/github/quipucords/yupana/python-3-shield.svg
