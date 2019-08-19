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

If for any reason Jenkins is not working, we have provided make commands and example `.env` files to allow you to deploy the latest changes to each Openshift project.

First make sure that you are logged into the correct cluster and project. Next, copy all of the `.env.deploy.example` files found in the `yupana.git/openshift/parameters/examples` directory to the `parameters` directory and remove the `.deploy.example` extension. Finally, uncomment & populate each variable with the correct information for the project that you are deploying to.

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