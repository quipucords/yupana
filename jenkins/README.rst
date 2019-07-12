=========================
Yupana Deployments README
=========================

|license| |Updates| |Python 3|

~~~~~
About
~~~~~

We deploy Yupana to the Insights Dev & Production Clusters (subscriptions-ci, subscriptions-qa, subscriptions-stage, subscriptions-prod) via Jenkins.

Getting Started
===============

You can access the deployment jobs via `Jenkins`_. The job names follow the pattern of ``deploy-yupana-PROJECT`` where ``PROJECT`` is either ci, qa, stage, or prod. A copy of the shell script used for deployment is located `here <deploy-yupana.sh>`_.
Any changes made to the deployment `template <../openshift/yupana-template.yaml>`_, should be enforced when the jenkins jobs are ran.

Deleting a Deployment
=====================

If it is ever necessary to delete the yupana deployment in any of the projects, you can do this by completing the following steps ::

    oc login https://api.insights-dev.openshift.com:443 --token=<your-token>
    oc project NAME_OF_PROJECT
    make oc-delete-yupana-data

After deleting the app, you can redeploy by rerunning the jenkins job by visiting `Jenkins`_ and choosing ``Build Now`` for the ``deploy-yupana-ci/qa/stage/prod`` projects.

Deploying without Jenkins
=========================

If for any reason Jenkins is not working, we have provided make commands that will create and deploy a new app, or refresh an existing one.

First make sure that you are logged into the correct cluster and project. Next, export the following environment variables with the correct values for the project you are deploying to::

    OPENSHIFT_PROJECT=<OPENSHIFT_PROJECT>
    KAFKA_HOST=<KAFKA_HOST>
    KAFKA_PORT=<KAFKA_PORT>
    KAFKA_NAMESPACE=<KAFKA_NAMESPACE>
    INSIGHTS_HOST_INVENTORY_URL=<INSIGHTS_HOST_INVENTORY_URL>
    DEPLOY_BUILD_VERSION=<DEPLOY_BUILD_VERSION>
    POSTGRES_SQL_SERVICE_HOST=<POSTGRES_SQL_SERVICE_HOST>

To deploy a new app to the project, run::

    make oc-new-app

To refresh an existing app, run::

    make oc-refresh

.. _Jenkins: https://sonar-jenkins.rhev-ci-vms.eng.rdu2.redhat.com/
.. |license| image:: https://img.shields.io/github/license/quipucords/yupana.svg
.. |Updates| image:: https://pyup.io/repos/github/quipucords/yupana/shield.svg
.. |Python 3| image:: https://pyup.io/repos/github/quipucords/yupana/python-3-shield.svg
