# yupana-centos7
FROM openshift/base-centos7

EXPOSE 8080

ENV PYTHON_VERSION=3.6 \
    PATH=$HOME/.local/bin/:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=UTF-8 \
    LC_ALL=en_US.UTF-8 \
    LANG=en_US.UTF-8 \
    PIP_NO_CACHE_DIR=off \
    ENABLE_PIPENV=true \
    STI_SCRIPTS_PATH="./s2i/" \
    APP_ROOT="yupana/" \
    APP_HOME="${APP_ROOT}/src/"

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

RUN INSTALL_PKGS="rh-python36 rh-python36-python-devel rh-python36-python-setuptools rh-python36-python-pip nss_wrapper \
        httpd24 httpd24-httpd-devel httpd24-mod_ssl httpd24-mod_auth_kerb httpd24-mod_ldap \
        httpd24-mod_session atlas-devel gcc-gfortran libffi-devel libtool-ltdl enchant" && \
    yum install -y centos-release-scl && \
    yum -y --setopt=tsflags=nodocs install --enablerepo=centosplus $INSTALL_PKGS && \
    rpm -V $INSTALL_PKGS && \
    # Remove centos-logos (httpd dependency) to keep image size smaller.
    rpm -e --nodeps centos-logos && \
    yum -y clean all --enablerepo='*'

# sets io.openshift.s2i.scripts-url label that way, or update that label
COPY ./openshift/s2i/bin/ $STI_SCRIPTS_PATH

# if we have any extra files we should copy them over
COPY openshift/root /

# Copy application files to the image.
COPY . ${APP_ROOT}/src

# - Create a Python virtual environment for use by any application to avoid
#   potential conflicts with Python packages preinstalled in the main Python
#   installation.
# - In order to drop the root user, we have to make some directories world
#   writable as OpenShift default security model is to run the container
#   under random UID.
#RUN source scl_source enable rh-python36 && \
#    virtualenv ${APP_ROOT} && \
#    chown -R 1001:0 ${APP_ROOT} && \
#    fix-permissions ${APP_ROOT} -P && \
#    rpm-file-permissions && \
#    $STI_SCRIPTS_PATH/assemble

USER 1001

# Set the default CMD
CMD $STI_SCRIPTS_PATH/run
