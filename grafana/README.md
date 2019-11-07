# Yupana Grafana README
We use grafana to display the metrics that we collect with prometheus.

## Getting Started

You can access the dashboards for the development cluster (the subscriptions-ci and subscriptions-qa project) [here](https://metrics-mnm-ci.5a9f.insights-dev.openshiftapps.com/). The dashboards are located in the `Provisioned Dashboards` folder and are named to match our project namespaces. You can access the dashboard for the production cluster (the subscriptions-prod project) [here](https://metrics.1b13.insights.openshiftapps.com/). It is simply called `Subscriptions`.

## Importing a dashboard

If it is ever necessary to import a dashboard for any of the projects, you can do the following:

1. Access the Grafana instance for either the development or production cluster (depending on which project you are working with)
2. Click on the `+` in the lefthand toolbar and select `Import`. Next, select `Upload .json file` in the upper right-hand corner. Now, import the dashboard of your choice located in this folder. Finally, click `Import` to begin using the grafana dashboard to visualize the data.
