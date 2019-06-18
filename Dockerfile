# yupana-centos7
FROM centos/python-36-centos7

EXPOSE 8080

ENV NODEJS_VERSION=8 \
    NODEJS_SCL=rh-nodejs8 \
    NPM_RUN=start \
    NODEJS_SCL=rh-nodejs8 \
    NPM_CONFIG_PREFIX=$HOME/.npm-global \
    PATH=$HOME/.local/bin/:$HOME/node_modules/.bin/:$HOME/.npm-global/bin/:$PATH \
    LC_ALL=en_US.UTF-8 \
    LANG=en_US.UTF-8 \
    PIP_NO_CACHE_DIR=off \
    ENABLE_PIPENV=true \
    APP_HOME="/opt/app-root/src/yupana" \
    APP_MODULE="config.wsgi"

ENV SUMMARY="Yupana is a subscriptions services application" \
    DESCRIPTION="Yupana is a subscriptions services application"

LABEL summary="$SUMMARY" \
    description="$DESCRIPTION" \
    io.k8s.description="$DESCRIPTION" \
    io.k8s.display-name="Yupana" \
    io.openshift.expose-services="8080:http" \
    io.openshift.tags="builder,python,python36,rh-python36" \
    com.redhat.component="python36-docker" \
    name="Yupana" \
    version="1" \
    maintainer="Red Hat Subscription Management Services"

USER root

# replace nodejs 6 with nodejs 8
RUN INSTALL_PKGS="${NODEJS_SCL} \
    ${NODEJS_SCL}-npm \
    ${NODEJS_SCL}-nodejs-nodemon \
    " && \
    yum-config-manager --enable centos-sclo-rh-testing && \
    yum -y --setopt=tsflags=nodocs install --enablerepo=centosplus $INSTALL_PKGS && \
    rpm -V $INSTALL_PKGS && \
    yum -y sed && \
    yum -y cat && \
    yum remove -y rh-nodejs6\* && \
    ln -s /usr/lib/node_modules/nodemon/bin/nodemon.js /usr/bin/nodemon && \
    yum -y clean all --enablerepo='*'

# sets io.openshift.s2i.scripts-url label that way, or update that label
COPY ./openshift/s2i/bin/ $STI_SCRIPTS_PATH

# if we have any extra files we should copy them over
COPY openshift/root /

# Copy application files to the image.
COPY . ${APP_ROOT}/src
RUN $STI_SCRIPTS_PATH/release
# - Create a Python virtual environment for use by any application to avoid
#   potential conflicts with Python packages preinstalled in the main Python
#   installation.
# - In order to drop the root user, we have to make some directories world
#   writable as OpenShift default security model is to run the container
#   under random UID.
RUN source scl_source enable rh-python36 rh-nodejs8 && \
    virtualenv ${APP_ROOT} && \
    chown -R 1001:0 ${APP_ROOT} && \
    fix-permissions ${APP_ROOT} -P && \
    rpm-file-permissions && \
    $STI_SCRIPTS_PATH/assemble

USER 1001

# Set the default CMD
CMD $STI_SCRIPTS_PATH/run
