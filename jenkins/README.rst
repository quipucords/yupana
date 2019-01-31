=========================
Yupana Deployments README
=========================

|license| |Updates| |Python 3|

~~~~~
About
~~~~~

We deploy Yupana to the Insights Dev Cluster (subscriptions-ci & subscriptions-qa) via Jenkins.

Getting Started
===============

You can access the deployment jobs via `Jenkins`_. The job for ci is called ``deploy-yupana-ci`` and the job for qa is called ``deploy-yupana-qa``. A copy of the shell script used for deployment is located `here <deploy-yupana.sh>`_.

Deployment
==========

If you make changes to the deployment `template <../openshift/yupana-template.yaml>`_, you must delete the yupana app within each project (subscriptions-ci, subscriptions-qa, subscriptions-stage, subscriptions-prod) and redeploy via Jenkins. You can do this by completing the following steps ::

    oc login https://api.insights-dev.openshift.com:443 --token=<your-token>
    oc project NAME_OF_PROJECT
    oc delete all -l app=yupana
    oc delete persistentvolumeclaim yupana-pgsql
    oc delete configmaps yupana-env
    oc delete secret yupana-secret
    oc delete secret yupana-pgsql

After deleting the app, you should visit `Jenkins`_ and choose ``Build Now`` for the ``deploy-yupana-ci/qa/stage/prod`` projects.

.. _Jenkins: https://sonar-jenkins.rhev-ci-vms.eng.rdu2.redhat.com/
.. |license| image:: https://img.shields.io/github/license/quipucords/yupana.svg
.. |Updates| image:: https://pyup.io/repos/github/quipucords/yupana/shield.svg
.. |Python 3| image:: https://pyup.io/repos/github/quipucords/yupana/python-3-shield.svg
