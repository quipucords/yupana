Working with OpenShift
======================

We are currently developing using OpenShift version 3.9. There are different setup requirements for Mac OS and Linux (instructions are provided for Fedora).

Run `oc cluster up` once before running the make commands to generate the referenced config file.

Openshift does offer shell/tab completion. It can be generated for either bash/zsh and is available by running `oc completion bash|zsh` The following example generates a shell script for completion and sources the file.  ::

    oc completion zsh > $HOME/.oc/oc_completion.sh
    source $HOME/.oc/oc_completion.sh

Running Locally in OpenShift
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To start the local cluster run the following:

.. code-block:: bash

    make oc-up

That will start a barebones OpenShift cluster that will persist configuration between restarts. It is possible that this command could result in the following when accessing the web console:
.. code-block::

    missing service (service "webconsole" not found)
    missing route (service "webconsole" not found)

If this is the case you can create the web-console namespace from the templates found https://github.com/openshift/origin/blob/v3.9.0/install/origin-web-console/console-config.yaml and https://github.com/openshift/origin/blob/v3.9.0/install/origin-web-console/console-template.yaml at by running the following:
.. code-block::

    make oc-fix

Once the cluster is running, you can deploy Yupana by running the following:
.. code-block::

    make oc-create-yupana

If you'd like to start the cluster and deploy Yupana run the following. Be warned that this could be inaccessible via the web-console if you run into the error mentioned above:

.. code-block:: bash

    make oc-up-dev

This will use the templates to create all the objects necessary to deploy **yupana**.

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


Mac OS
-------

There is a known issue with Docker for Mac ignoring `NO_PROXY` settings which are required for OpenShift. (https://github.com/openshift/origin/issues/18596) The current solution is to use a version of Docker prior to 17.12.0-ce, the most recent of which can be found at `docker-community-edition-17091-ce-mac42-2017-12-11`_

Docker needs to be configured for OpenShift. A local registry and proxy are used by OpenShift and Docker needs to be made aware.

Add `172.30.0.0/16` to the Docker insecure registries which can be accomplished from Docker -> Preferences -> Daemon. This article details information about insecure registries `Test an insecure registry | Docker Documentation`_

Add `172.30.1.1` to the list of proxies to bypass. This can be found at Docker -> Preferences -> Proxies

.. _`Getting Started with Docker on Fedora`: https://developer.fedoraproject.org/tools/docker/docker-installation.html
.. _`OpenShift — Fedora Developer Portal`: https://developer.fedoraproject.org/deployment/openshift/about.html
.. _`docker-community-edition-17091-ce-mac42-2017-12-11`: https://docs.docker.com/docker-for-mac/release-notes/#docker-community-edition-17091-ce-mac42-2017-12-11
.. _`Test an insecure registry | Docker Documentation`: https://docs.docker.com/registry/insecure/


Troubleshooting
---------------

OpenShift uses Docker to run containers. When running a cluster locally for developement, deployment can be strained by low resource allowances in Docker. For development it is recommended that Docker have at least 4 GB of memory available for use.

Also, if Openshift services misbehave or do not deploy properly, it can be useful to spin the cluster down, restart the Docker service and retry.
