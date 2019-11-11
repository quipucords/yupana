# Working with OpenShift
We are currently developing using OpenShift version 3.9. There are different setup requirements for Mac OS and Linux (instructions are provided for Fedora). To install the openshift client, visit https://access.redhat.com/downloads/content/290/ver=3.9/rhel---7/3.9.43/x86_64/product-software and follow the instructions to download the oc archive. Once you have downloaded the archive, unpack it and move the oc binary to a location such as `/Users/your-user/dev/bin/openshift/`. Now edit your `~/.bash_profile` to define `PATH` as the following:

```
    PATH="PATH_TO_OPENSHIFT_OC/:${PATH}"
    export PATH
```

Restart your shell. Run `oc cluster up` once before running the make commands to generate the referenced config file.

## Setup Requirements for Mac OS

There is a known issue with Docker for Mac ignoring `NO_PROXY` settings which are required for OpenShift. (https://github.com/openshift/origin/issues/18596) The current solution is to use a version of Docker prior to 17.12.0-ce, the most recent of which can be found at [docker-community-edition-17091-ce-mac42-2017-12-11](https://docs.docker.com/docker-for-mac/release-notes/#docker-community-edition-17091-ce-mac42-2017-12-11).

Docker needs to be configured for OpenShift. A local registry and proxy are used by OpenShift and Docker needs to be made aware.

Add `172.30.0.0/16` to the Docker insecure registries which can be accomplished from Docker -> Preferences -> Daemon. This article details information about insecure registries [Test an insecure registry | Docker Documentation](https://docs.docker.com/registry/insecure/).

Add `172.30.1.1` to the list of proxies to bypass. This can be found at Docker -> Preferences -> Proxies

## Running Locally in OpenShift

Yupana is deployed using the pipeline defined in the [e2e-deploy repo](https://github.com/RedHatInsights/e2e-deploy).
You should clone the e2e-deploy repo in order to run the project in openshift locally in order to mimic the way that deployments to production work. One of the main assumptions when deploying via e2e is that the secrets are already defined in an existing Openshift project.

Therefore, the only template that is stored locally is the `secret.yaml` for our project.

The `Makefile` target to create the secrets includes scripting to dynamically pass parameter values into the OpenShift templates. A developer may define parameter values by placing a parameter file for the secrets into the `yupana.git/openshift/parameters` directory.

An example of a secret parameter file is provided for you in the `yupana.git/openshift/parameters/examples` directory. For development, you can copy the example environment file into the `parameters` directory and remove the `.example` extension.

Once you have copied over the env file for the secrets template, you can begin deploying using the provided make commands. To start a barebones OpenShift cluster that will persist configuration between restarts, you can run the following:

```

    make oc-up

```

 Once the cluster is running, you can create the secrets by running the following:

```

    make oc-create-secret

```
Now switch to the `e2e-deploy` repo and enter a virtual environment.

1. Edit the [buildfactory/subscriptions/yupana.yml](https://github.com/RedHatInsights/e2e-deploy/blob/master/buildfactory/subscriptions/yupana.yml#L241) and change the `SOURCE_REPOSITORY_REF` to have a value that is set to the name of the branch of Yupana that you are testing.

2. Edit the [templates/subscriptions/yupana.yml](https://github.com/RedHatInsights/e2e-deploy/blob/master/templates/subscriptions/yupana.yml#L440) to set the `IMAGE_NAMESPACE` to yupana instead of buildfactory.

3. Now to deploy the deployment configs and to deploy PostgreSQL, run the following:

```

    ocdeployer deploy -s subscriptions -e env/ci.yml --secrets-src-project yupana yupana

```

4. Now to create the imagestream and run the build configs, run the following:

```

    ocdeployer deploy -s subscriptions --template-dir buildfactory --secrets-src-project yupana yupana

```

To stop the local cluster run the following:

```

    make oc-down

```

Since all cluster information is preserved, you are then able to start the cluster back up with `make oc-up` and resume right where you have left off.

If you'd like to remove all your saved settings for your cluster, you can run the following:

```

    make oc-clean

```

There are also other make targets available to step through the project deployment. See `make help` for more information about available targets.

```

## Fedora

The setup process for Fedora is well outlined in two articles.
First, get Docker up and running. [Getting Started with Docker on Fedora](https://developer.fedoraproject.org/tools/docker/docker-installation.html).

Then follow these instructions to get OpenShift setup [OpenShift — Fedora Developer Portal](https://developer.fedoraproject.org/deployment/openshift/about.html).

## Troubleshooting

OpenShift uses Docker to run containers. When running a cluster locally for developement, deployment can be strained by low resource allowances in Docker. For development it is recommended that Docker have at least 4 GB of memory available for use.

Also, if Openshift services misbehave or do not deploy properly, it can be useful to spin the cluster down, restart the Docker service and retry.
