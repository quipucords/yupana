/*
* Requires: https://github.com/RedHatInsights/insights-pipeline-lib
*/

@Library("github.com/RedHatInsights/insights-pipeline-lib@v3") _


// this 'if' statement makes sure this is a PR, so we don't run smoke tests again
// after code has been merged into the stable branch.
if (env.CHANGE_ID) {
    execSmokeTest (
        // the service-set/component for this app in e2e-deploy "buildfactory"
        ocDeployerBuilderPath: "subscriptions/yupana",
        // the service-set/component for this app in e2e-deploy "templates"
        ocDeployerComponentPath: "subscriptions",
        // the service sets to deploy into the test environment
        ocDeployerServiceSets: "ingress,inventory,platform-mq,subscriptions",
        // the iqe plugins to install for the test
        iqePlugins: ["iqe-yupana-plugin"],
        // the pytest marker to use when calling `iqe tests all`
        pytestMarker: "yupana_smoke",
        // optional id for an IQE configuration file stored as a secret in Jenkins
        //configFileCredentialsId: "myId",
    )
}
