# Yupana Deployments README
We deploy Yupana to the Insights Dev & Production Clusters (subscriptions-ci, subscriptions-qa, subscriptions-stage, subscriptions-prod) via Jenkins.

## Getting Started

You can access the deployment jobs via [Jenkins](https://sonar-jenkins.rhev-ci-vms.eng.rdu2.redhat.com/). The job names follow the pattern of `deploy-yupana-PROJECT` where `PROJECT` is either ci, qa, stage, or prod. A copy of the shell script used for deployment is located `here <deploy-yupana.sh>`_.
Any changes made to the deployment `template <../openshift/yupana-template.yaml>`_, should be enforced when the jenkins jobs are ran.

## Deleting a Deployment

If it is ever necessary to delete the yupana deployment in any of the projects, you can do this by completing the following steps:

```
oc login https://api.insights-dev.openshift.com:443 --token=<your-token>
oc project NAME_OF_PROJECT
make oc-delete-yupana-data
```

After deleting the app, you can redeploy by rerunning the jenkins job by visiting [Jenkins](https://sonar-jenkins.rhev-ci-vms.eng.rdu2.redhat.com/) and choosing `Build Now` for the `deploy-yupana-ci/qa/stage/prod` projects.


## Deploying without Jenkins

If for any reason Jenkins is not working, we have provided make commands that will create and deploy all of the services and objects for a new app, or refresh an existing one.

First make sure that you are logged into the correct cluster and project. Next, make sure that you have the correct values set in your parameter files. Examples of parameter files are provided in the `yupana.git/openshift/parameters/examples` directory. Typically, you will need the following defined in the correct `.env` file for the environment that you want to deploy to:
```
NAMESPACE=<OPENSHIFT_NAMESPACE>
OPENSHIFT_PROJECT=<OPENSHIFT_PROJECT>
KAFKA_HOST=<KAFKA_HOST>
KAFKA_PORT=<KAFKA_PORT>
KAFKA_NAMESPACE=<KAFKA_NAMESPACE>
INSIGHTS_HOST_INVENTORY_URL=<INSIGHTS_HOST_INVENTORY_URL>
DEPLOY_BUILD_VERSION=<DEPLOY_BUILD_VERSION>
POSTGRES_SQL_SERVICE_HOST=<POSTGRES_SQL_SERVICE_HOST>
```

To deploy to PROD you will need the following in addition to the above:
```
DATABASE_ADMIN_PASSWORD=<DATABASE_ADMIN_PASSWORD>
DATABASE_PASSWORD=<DATABASE_PASSWORD>
DATABASE_USER=<DATABASE_USER>
DATABASE_SERVICE_CERT=<DATABASE_SERVICE_CERT>
```

To deploy a new app to the project, you can run the following where `NAMESPACE` is set to the environment that you wish to deploy to:
```
make oc-create-yupana-and-db NAMESPACE=subscriptions-ci
```

To refresh an existing app, run:
```
make oc-refresh NAMESPACE=subscriptions-ci
```

To deploy to production, you will not need the db, so you can run:
```
make oc-create-yupana-app NAMESPACE=subscriptions-prod
```