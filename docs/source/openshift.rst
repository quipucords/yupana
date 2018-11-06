Working with OpenShift
======================

We are currently developing using OpenShift version 3.9. There are different setup requirements for Mac OS and Linux (instructions are provided for Fedora). To install the openshift client, visit https://access.redhat.com/downloads/content/290/ver=3.9/rhel---7/3.9.43/x86_64/product-software and follow the instructions to download the oc archive. Once you have downloaded the archive, unpack it and move the oc binary to a location such as ``/Users/your-user/dev/bin/openshift/``. Now edit your .bash_profile to define ``PATH`` as the following::

    PATH="PATH_TO_OPENSHIFT_OC/:${PATH}"
    export PATH

Restart your shell. Run ``oc cluster up`` once before running the make commands to generate the referenced config file.

Setup Requirements for Mac OS
-----------------------------

There is a known issue with Docker for Mac ignoring `NO_PROXY` settings which are required for OpenShift. (https://github.com/openshift/origin/issues/18596) The current solution is to use a version of Docker prior to 17.12.0-ce, the most recent of which can be found at `docker-community-edition-17091-ce-mac42-2017-12-11`_

Docker needs to be configured for OpenShift. A local registry and proxy are used by OpenShift and Docker needs to be made aware.

Add `172.30.0.0/16` to the Docker insecure registries which can be accomplished from Docker -> Preferences -> Daemon. This article details information about insecure registries `Test an insecure registry | Docker Documentation`_

Add `172.30.1.1` to the list of proxies to bypass. This can be found at Docker -> Preferences -> Proxies

.. _`Getting Started with Docker on Fedora`: https://developer.fedoraproject.org/tools/docker/docker-installation.html
.. _`OpenShift — Fedora Developer Portal`: https://developer.fedoraproject.org/deployment/openshift/about.html
.. _`docker-community-edition-17091-ce-mac42-2017-12-11`: https://docs.docker.com/docker-for-mac/release-notes/#docker-community-edition-17091-ce-mac42-2017-12-11
.. _`Test an insecure registry | Docker Documentation`: https://docs.docker.com/registry/insecure/

Running Locally in OpenShift
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To start the local cluster run the following:

.. code-block:: bash

    make oc-up

That will start a barebones OpenShift cluster that will persist configuration between restarts. Once the cluster is running, you can deploy Yupana by running the following:

.. code-block:: bash

    make oc-create-yupana

If you'd like to start the cluster and deploy Yupana, run the following:

.. code-block:: bash

    make oc-up-dev

This will use the templates to create all the objects necessary.

To stop the local cluster run the following:

.. code-block:: bash

    make oc-down

Since all cluster information is preserved, you are then able to start the cluster back up with ``make oc-up`` and resume right where you have left off.

If you'd like to remove all your saved settings for your cluster, you can run the following:

.. code-block:: bash

    make oc-clean

There are also other make targets available to step through the project deployment.

Fedora
------

The setup process for Fedora is well outlined in two articles.
First, get Docker up and running. `Getting Started with Docker on Fedora`_.

Then follow these instructions to get OpenShift setup `OpenShift — Fedora Developer Portal`_.

Troubleshooting
---------------

OpenShift uses Docker to run containers. When running a cluster locally for developement, deployment can be strained by low resource allowances in Docker. For development it is recommended that Docker have at least 4 GB of memory available for use.

Also, if Openshift services misbehave or do not deploy properly, it can be useful to spin the cluster down, restart the Docker service and retry.
