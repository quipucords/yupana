curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (STATUS) - Deploy build started.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}

# for ci/qa projects, we need to change the OPENSHIFT_TOKEN to PREPROD_OPENSHIFT_TOKEN
# for stage/prod projects, we need to change the OPENSHIFT_TOKEN to PROD_OPENSHIFT_TOKEN
oc login https://${OPENSHIFT_HOST}:${OPENSHIFT_PORT} --token=${OPENSHIFT_TOKEN}

oc project ${OPENSHIFT_PROJECT}


# Is this a new object deployment or an existing? Decide based on whether the object exists
IMAGESTREAM=`oc get -o name 'is/python-36-centos7' 2>/dev/null | tail -1 | awk '{print $1}'`
CONFIGMAP=`oc get configmap -l app=${APP_NAME} 2>/dev/null | tail -1 | awk '{print $1}'`
SECRET=`oc get secret -l app=${APP_NAME} 2>/dev/null | tail -1 | awk '{print $1}'`
DB_CONFIG=`oc get dc ${APP_NAME}-db 2>/dev/null | tail -1 | awk '{print $1}'`
APP_BUILD_CONFIG=`oc get bc ${APP_NAME} 2>/dev/null | tail -1 | awk '{print $1}'`

if [ -z "$IMAGESTREAM" ]; then

# no imagestream so create a new one
  echo "Creating imagestream."
  oc process -f ${YUPANA_IMAGESTREAM_TEMPLATE_PATH} \
      --param NAMESPACE=${OPENSHIFT_PROJECT} \
      | oc create --save-config=True -n ${OPENSHIFT_PROJECT} -f - 2>&1 | grep -v "already exists" || /usr/bin/true ;\
else
  echo "Imagestream exists."
  oc process -f ${YUPANA_IMAGESTREAM_TEMPLATE_PATH} \
      --param NAMESPACE=${OPENSHIFT_PROJECT} \
      | oc apply -f -
fi

if [ -z "$CONFIGMAP" ]; then
  echo "Creating configmap."
  oc process -f ${YUPANA_CONFIGMAP_TEMPLATE_PATH} \
      --param NAMESPACE=${OPENSHIFT_PROJECT} \
      --param KAFKA_HOST=${KAFKA_HOST} \
      --param KAFKA_PORT=${KAFKA_PORT} \
      --param KAFKA_NAMESPACE=${KAFKA_NAMESPACE} \
      --param INSIGHTS_HOST_INVENTORY_URL=${INSIGHTS_HOST_INVENTORY_URL} \
      --param POSTGRES_SQL_SERVICE_HOST=${POSTGRES_SQL_SERVICE_HOST} \
      --param MINIMUM_REPLICAS=${MINIMUM_REPLICAS} \
      --param MAXIMUM_REPLICAS=${MAXIMUM_REPLICAS} \
      --param TARGET_CPU_UTILIZATION=${TARGET_CPU_UTILIZATION} \
      --param HOSTS_PER_REQ=${HOSTS_PER_REQ} \
      --param MAX_THREADS=${MAX_THREADS} \
      --param BUILD_VERSION=${BUILD_VERSION} \
      --param PAUSE_KAFKA_FOR_FILE_UPLOAD_SERVICE=${PAUSE_KAFKA_FOR_FILE_UPLOAD_SERVICE} \
      --param HOST_INVENTORY_UPLOAD_MODE=${HOST_INVENTORY_UPLOAD_MODE} \
      --param HOSTS_UPLOAD_TIMEOUT=${HOSTS_UPLOAD_TIMEOUT} \
      --param HOSTS_UPLOAD_FUTURES_COUNT=${HOSTS_UPLOAD_FUTURES_COUNT} \
      | oc create --save-config=True -n ${OPENSHIFT_PROJECT} -f - 2>&1 | grep -v "already exists" || /usr/bin/true ;\

else
  echo "Updating configmap."
  oc process -f ${YUPANA_CONFIGMAP_TEMPLATE_PATH} \
      --param NAMESPACE=${OPENSHIFT_PROJECT} \
      --param KAFKA_HOST=${KAFKA_HOST} \
      --param KAFKA_PORT=${KAFKA_PORT} \
      --param KAFKA_NAMESPACE=${KAFKA_NAMESPACE} \
      --param INSIGHTS_HOST_INVENTORY_URL=${INSIGHTS_HOST_INVENTORY_URL} \
      --param POSTGRES_SQL_SERVICE_HOST=${POSTGRES_SQL_SERVICE_HOST} \
      --param MINIMUM_REPLICAS=${MINIMUM_REPLICAS} \
      --param MAXIMUM_REPLICAS=${MAXIMUM_REPLICAS} \
      --param TARGET_CPU_UTILIZATION=${TARGET_CPU_UTILIZATION} \
      --param HOSTS_PER_REQ=${HOSTS_PER_REQ} \
      --param MAX_THREADS=${MAX_THREADS} \
      --param BUILD_VERSION=${BUILD_VERSION} \
      --param PAUSE_KAFKA_FOR_FILE_UPLOAD_SERVICE=${PAUSE_KAFKA_FOR_FILE_UPLOAD_SERVICE} \
      --param HOST_INVENTORY_UPLOAD_MODE=${HOST_INVENTORY_UPLOAD_MODE} \
      --param HOSTS_UPLOAD_TIMEOUT=${HOSTS_UPLOAD_TIMEOUT} \
      --param HOSTS_UPLOAD_FUTURES_COUNT=${HOSTS_UPLOAD_FUTURES_COUNT} \
      | oc apply -f -
fi

if [ -z "$SECRET" ]; then

# no secret exists for yupana
  echo "Creating secret for yupana."
  oc process -f ${YUPANA_SECRET_TEMPLATE_PATH} \
      --param NAMESPACE=${OPENSHIFT_PROJECT} \
      --param DATABASE_PASSWORD=${DATABASE_PASSWORD} \
      --param DATABASE_USER=${DATABASE_USER} \
      | oc create --save-config=True -n ${OPENSHIFT_PROJECT} -f - 2>&1 | grep -v "already exists" || /usr/bin/true ;\

else
  echo "Updating yupana secret."
  oc process -f ${YUPANA_SECRET_TEMPLATE_PATH} \
      --param NAMESPACE=${OPENSHIFT_PROJECT} \
      --param DATABASE_PASSWORD=${DATABASE_PASSWORD} \
      --param DATABASE_USER=${DATABASE_USER} \
      | oc apply -f -
fi

if [ -z "$DB_CONFIG" ]; then
  echo "Creating DB config."
  oc process -f ${YUPANA_DB_TEMPLATE_PATH} \
      --param NAMESPACE=${OPENSHIFT_PROJECT} \
      | oc create --save-config=True -n ${OPENSHIFT_PROJECT} -f - 2>&1 | grep -v "already exists" || /usr/bin/true ;\

else
  echo "Updating DB config."
  oc process -f ${YUPANA_DB_TEMPLATE_PATH} \
      --param NAMESPACE=${OPENSHIFT_PROJECT} \
      | oc apply -f -
fi

if [ -z "$APP_BUILD_CONFIG" ]; then

# no app found so create a new one
  echo "Create a new app"
  curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (STATUS) - Create a new app.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}

  oc process -f ${YUPANA_APP_TEMPLATE_PATH}  \
      --param NAMESPACE=${OPENSHIFT_PROJECT} \
      --param SOURCE_REPOSITORY_URL=${GIT_URL} \
      --param SOURCE_REPOSITORY_REF=${GIT_BRANCH} \
      --param KAFKA_HOST=${KAFKA_HOST} \
      --param KAFKA_PORT=${KAFKA_PORT} \
      --param KAFKA_NAMESPACE=${KAFKA_NAMESPACE} \
      --param INSIGHTS_HOST_INVENTORY_URL=${INSIGHTS_HOST_INVENTORY_URL} \
      --param MINIMUM_REPLICAS=${MINIMUM_REPLICAS} \
      --param MAXIMUM_REPLICAS=${MAXIMUM_REPLICAS} \
      --param TARGET_CPU_UTILIZATION=${TARGET_CPU_UTILIZATION} \
      --param HOSTS_PER_REQ=${HOSTS_PER_REQ} \
      --param MAX_THREADS=${MAX_THREADS} \
      --param BUILD_VERSION=${BUILD_VERSION} \
      --param PAUSE_KAFKA_FOR_FILE_UPLOAD_SERVICE=${PAUSE_KAFKA_FOR_FILE_UPLOAD_SERVICE} \
      --param POSTGRES_SQL_SERVICE_HOST=${POSTGRES_SQL_SERVICE_HOST} \
      --param HOST_INVENTORY_UPLOAD_MODE=${HOST_INVENTORY_UPLOAD_MODE} \
      --param DATABASE_PASSWORD=${DATABASE_PASSWORD} \
      --param DATABASE_USER=${DATABASE_USER} \
      --param HOSTS_UPLOAD_TIMEOUT=${HOSTS_UPLOAD_TIMEOUT} \
      --param HOSTS_UPLOAD_FUTURES_COUNT=${HOSTS_UPLOAD_FUTURES_COUNT} \
      | oc create --save-config=True -n ${OPENSHIFT_PROJECT} -f - 2>&1 | grep -v "already exists" || /usr/bin/true ;\

  echo "Find build id"
  BUILD_ID=`oc get builds | grep ${APP_NAME} | tail -1 | awk '{print $1}'`
  rc=1
  attempts=75
  count=0
  while [ $rc -ne 0 -a $count -lt $attempts ]; do
    BUILD_ID=`oc get builds | grep ${APP_NAME} | tail -1 | awk '{print $1}'`
    if [ $BUILD_ID == "NAME" ]; then
      count=$(($count+1))
      echo "Attempt $count/$attempts"
      sleep 5
    else
      rc=0
      echo "Build Id is :" ${BUILD_ID}
    fi
  done

  if [ $rc -ne 0 ]; then
    echo "Fail: Build could not be found after maximum attempts"
	curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (FAIL) - Build could not be found after maximum attempts.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
    exit 1
  fi
else

  # Application already exists, just need to start a new build
  echo "App Exists. Triggering application build and deployment"
  curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (STATUS) - App Exists. Triggering application build and deployment.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}

  oc process -f ${YUPANA_APP_TEMPLATE_PATH}  \
      --param NAMESPACE=${OPENSHIFT_PROJECT} \
      --param SOURCE_REPOSITORY_URL=${GIT_URL} \
      --param SOURCE_REPOSITORY_REF=${GIT_BRANCH} \
      --param KAFKA_HOST=${KAFKA_HOST} \
      --param KAFKA_PORT=${KAFKA_PORT} \
      --param KAFKA_NAMESPACE=${KAFKA_NAMESPACE} \
      --param INSIGHTS_HOST_INVENTORY_URL=${INSIGHTS_HOST_INVENTORY_URL} \
      --param MINIMUM_REPLICAS=${MINIMUM_REPLICAS} \
      --param MAXIMUM_REPLICAS=${MAXIMUM_REPLICAS} \
      --param TARGET_CPU_UTILIZATION=${TARGET_CPU_UTILIZATION} \
      --param HOSTS_PER_REQ=${HOSTS_PER_REQ} \
      --param MAX_THREADS=${MAX_THREADS} \
      --param BUILD_VERSION=${BUILD_VERSION} \
      --param PAUSE_KAFKA_FOR_FILE_UPLOAD_SERVICE=${PAUSE_KAFKA_FOR_FILE_UPLOAD_SERVICE} \
      --param POSTGRES_SQL_SERVICE_HOST=${POSTGRES_SQL_SERVICE_HOST} \
      --param HOST_INVENTORY_UPLOAD_MODE=${HOST_INVENTORY_UPLOAD_MODE} \
      --param DATABASE_PASSWORD=${DATABASE_PASSWORD} \
      --param DATABASE_USER=${DATABASE_USER} \
      --param HOSTS_UPLOAD_TIMEOUT=${HOSTS_UPLOAD_TIMEOUT} \
      --param HOSTS_UPLOAD_FUTURES_COUNT=${HOSTS_UPLOAD_FUTURES_COUNT} \
      | oc apply -f -
  BUILD_ID=`oc start-build ${APP_BUILD_CONFIG} | awk '{print $2}' | sed -e 's/^"//' -e 's/"$//'`
fi

echo "Waiting for build ${BUILD_ID} to start"
curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (STATUS) - Waiting for build ${BUILD_ID} to start.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
rc=1
attempts=25
count=0
while [ $rc -ne 0 -a $count -lt $attempts ]; do
  status=`oc get build ${BUILD_ID} -o jsonpath='{.status.phase}'`
  if [[ $status == "Failed" || $status == "Error" || $status == "Canceled" ]]; then
    echo "Fail: Build completed with unsuccessful status: ${status}"
    curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (FAIL) - Build completed with unsuccessful status: ${status}.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
    exit 1
  fi
  if [ $status == "Complete" ]; then
    echo "Build completed successfully, will test deployment next"
    curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (STATUS) - Build completed successfully, will test deployment next.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
    rc=0
  fi

  if [ $status == "Running" ]; then
    echo "Build started"
    curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (STATUS) - Build started.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
    rc=0
  fi

  if [ $status == "Pending" ]; then
    count=$(($count+1))
    echo "Attempt $count/$attempts"
    sleep 5
  fi
done

# stream the logs for the build that just started
oc logs -f bc/${APP_NAME}


echo "Checking build result status"
rc=1
count=0
attempts=100
while [ $rc -ne 0 -a $count -lt $attempts ]; do
  status=`oc get build ${BUILD_ID} -o jsonpath='{.status.phase}'`
  if [[ $status == "Failed" || $status == "Error" || $status == "Canceled" ]]; then
    echo "Fail: Build completed with unsuccessful status: ${status}"
    curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (FAIL) - Build completed with unsuccessful status: ${status}.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
    exit 1
  fi

  if [ $status == "Complete" ]; then
    echo "Build completed successfully, will test deployment next"
    curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (STATUS) - Build completed successfully, will test deployment next.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
    rc=0
  else
    count=$(($count+1))
    echo "Attempt $count/$attempts"
    sleep 5
  fi
done

if [ $rc -ne 0 ]; then
    echo "Fail: Build did not complete in a reasonable period of time"
    curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (FAIL) - Build did not complete in a reasonable period of time.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
    exit 1
fi

# scale up the test deployment
# RC_ID=`oc get rc | tail -1 | awk '{print $1}'`

# echo "Scaling up new deployment $test_rc_id"
# oc scale --replicas=1 rc $RC_ID

echo "Checking for successful deployment at http://${APP_NAME}-${OPENSHIFT_PROJECT}.${OPENSHIFT_APP_DOMAIN}${APP_READINESS_PROBE}"
set +e
rc=1
count=0
attempts=100
while [ $rc -ne 0 -a $count -lt $attempts ]; do
  CURL_RES=`curl -I --connect-timeout 2 http://${APP_NAME}-${OPENSHIFT_PROJECT}.${OPENSHIFT_APP_DOMAIN}${APP_READINESS_PROBE} 2> /dev/null | head -n 1 | cut -d$' ' -f2`
  if [[ $CURL_RES == "200" ]]; then
    rc=0
    echo "Successful test against http://${APP_NAME}-${OPENSHIFT_PROJECT}.${OPENSHIFT_APP_DOMAIN}${APP_READINESS_PROBE}"
    curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (SUCCESS) - Successful test against http://${APP_NAME}-${OPENSHIFT_PROJECT}.${OPENSHIFT_APP_DOMAIN}${APP_READINESS_PROBE}.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
    break
  fi
  count=$(($count+1))
  echo "Attempt $count/$attempts"
  sleep 5
done
set -e

if [ $rc -ne 0 ]; then
    echo "Failed to access deployment, aborting roll out."
    curl -d "{\"text\": \"${OPENSHIFT_PROJECT}/${APP_NAME} (FAIL) - Failed to access deployment, aborting roll out.\"}" -H "Content-Type: application/json" -X POST ${SLACK_QPC_BOTS}
    exit 1
fi